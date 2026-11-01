"""Canvas-facing adapters for the unified Qwen image workbench."""
from __future__ import annotations

import copy
import re

from .image_tools import CodexImageUpscale, CodexImageOutput, CodexWorkflowControls
from .prompt_optimize import DEFAULT_RULES
from .static_prompt import REPAIR_RULE
from .qwen_studio import FINISH_INPUT_SIZES, resize_input, input_dimensions


class CodexQwenStudioControls(CodexWorkflowControls):
    pass


class CodexQwenStudioPrompt:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            '原始提示词': ('STRING', {'default': '', 'multiline': True}),
            '生成前置提示词': ('STRING', {'default': '', 'multiline': True}),
        }, 'optional': {'参考图集': ('CODEX_REFERENCES',),
                        '外接提示词': ('STRING', {'forceInput': True, 'lazy': True})},
                'hidden': {'prompt': 'PROMPT', 'extra_pnginfo': 'EXTRA_PNGINFO', 'unique_id': 'UNIQUE_ID'}}

    RETURN_TYPES = ('STRING',)
    RETURN_NAMES = ('生图提示词',)
    FUNCTION = 'compose'
    CATEGORY = 'Codex/千问工作台'

    def check_lazy_status(self, **kwargs):
        return ['外接提示词'] if '外接提示词' in kwargs and kwargs['外接提示词'] is None else []

    def compose(self, 原始提示词='', 生成前置提示词='', 参考图集=None, 外接提示词=None,
                prompt=None, extra_pnginfo=None, unique_id=None):
        # Dictation may have been inserted into the middle of the original text.
        # Its linked node still returns the transcript; do not replace the full
        # editor contents with that shorter transcript during graph execution.
        workflow_nodes = (extra_pnginfo or {}).get('workflow', {}).get('nodes', [])
        saved = next((node for node in workflow_nodes if str(node.get('id')) == str(unique_id)), {})
        source_id = saved.get('properties', {}).get('bbaigc_speech_source_id')
        sources = prompt or {}
        connection = sources.get(str(unique_id), {}).get('inputs', {}).get('外接提示词')
        linked_id = str(connection[0]) if isinstance(connection, list) and len(connection) == 2 else None
        seen = set()
        while linked_id is not None and linked_id not in seen and 'reroute' in sources.get(linked_id, {}).get('class_type', '').lower():
            seen.add(linked_id)
            link = next((value for value in sources[linked_id].get('inputs', {}).values()
                         if isinstance(value, list) and len(value) == 2), None)
            linked_id = str(link[0]) if link else None
        inserted_speech = (source_id is not None and linked_id == str(source_id) and
                           sources.get(str(source_id), {}).get('class_type') == 'CodexSpeechInput')
        if not inserted_speech and 外接提示词 is not None and str(外接提示词).strip():
            external = str(外接提示词).strip()
            if not str(原始提示词).rstrip().endswith(external):
                原始提示词 = external
        text = '\n'.join(value.strip() for value in (生成前置提示词, 原始提示词) if value.strip())
        if '@未引用[' in text:
            raise ValueError('提示词含未引用的参考图，请点击输入框中的红色标记重新选择或删除。')
        slots = (参考图集 or {}).get('image_slots', {})

        def replace(match):
            identity = 'ref' + match.group(1)
            slot = slots.get(identity)
            if slot is None:
                raise ValueError(f'提示词引用的参考图{match.group(1)}未启用，请检查图库和生成模式。')
            return f'<image{slot}>'

        # One substitution pass prevents slot renumbering from changing earlier replacements.
        text = re.sub(r'(?:@?(?:参考图|图)|@ref)([1-9]\d*)(?!\d)', replace, text, flags=re.I)
        records = [{**entry, 'slot': slots.get(entry['id'], entry.get('slot'))}
                   for entry in (参考图集 or {}).get('entries', [])]
        from .output_metadata import remember_source_prompt
        remember_source_prompt(prompt, unique_id, 原始提示词)
        return {'ui': {'codex_source_text': [原始提示词], 'codex_reference_map': [records]}, 'result': (text,)}


class CodexQwenStudioRules:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {
            '生图前置': ('STRING', {'default': '', 'multiline': True}),
            '优化前置': ('STRING', {'default': DEFAULT_RULES, 'multiline': True}),
            '修复前置': ('STRING', {'default': REPAIR_RULE, 'multiline': True}),
        }}

    RETURN_TYPES = ('STRING', 'STRING', 'STRING')
    RETURN_NAMES = ('生图前置', '优化前置', '修复前置')
    FUNCTION = 'execute'
    CATEGORY = 'Codex/千问工作台'

    def execute(self, 生图前置='', 优化前置=DEFAULT_RULES, 修复前置=REPAIR_RULE):
        return 生图前置, 优化前置, 修复前置


class CodexQwenStudioUpscale(CodexImageUpscale):
    @classmethod
    def INPUT_TYPES(cls):
        result = copy.deepcopy(super().INPUT_TYPES())
        result['required'] = {'启用超分': ('BOOLEAN', {'default': False}), **result['required']}
        result['required']['超分路线'] = (['SeedVR2', 'VOSR2', 'DLSS5→VOSR2'], {'default': 'SeedVR2'})
        result.setdefault('optional', {}).update({
            '输入尺寸': (FINISH_INPUT_SIZES, {'default': '原图', 'tooltip': '仅限制超分前输入长边，随后仍按所选超分倍率放大。'}),
            '输入长边': ('INT', {'default': 1024, 'min': 256, 'max': 4096, 'step': 32}),
        })
        return result

    RETURN_TYPES = ('IMAGE', 'BOOLEAN')
    RETURN_NAMES = ('超分或直通图像', '超分已启用')

    @classmethod
    def VALIDATE_INPUTS(cls, 启用超分=False, **kwargs):
        return True if not 启用超分 else super().VALIDATE_INPUTS(**kwargs)

    def upscale(self, 图像, 启用超分=False, 输入尺寸='原图', 输入长边=1024, **kwargs):
        if not 启用超分:
            return (图像, False)
        original = 图像
        prepared = resize_input(图像, 输入尺寸, 输入长边)
        result = super().upscale(图像=prepared, **kwargs)
        values = result.get('result', ()) if isinstance(result, dict) else result
        image = values[0]
        factor = float(kwargs.get('总倍率', 2))
        expected_width, expected_height = round(int(prepared.shape[2])*factor), round(int(prepared.shape[1])*factor)
        actual_width, actual_height = int(image.shape[2]), int(image.shape[1])
        if kwargs.get('超分路线') == 'DLSS5→VOSR2':
            dlss, vosr = float(kwargs.get('DLSS倍率', 1)), int(kwargs.get('VOSR倍率', 2))
            expected_width = (round(int(prepared.shape[2])*dlss)//2*2 if dlss != 1 else int(prepared.shape[2]))*vosr
            expected_height = (round(int(prepared.shape[1])*dlss)//2*2 if dlss != 1 else int(prepared.shape[1]))*vosr
        # Small lattice rounding from an external upscaler is tolerated; a whole
        # missing scale step must never be presented as a successful 2x result.
        tolerance = 2 if kwargs.get('超分路线') == 'DLSS5→VOSR2' else 1
        if abs(actual_width-expected_width) > tolerance or abs(actual_height-expected_height) > tolerance:
            raise RuntimeError(f'超分尺寸与{factor:g}倍不符：实际输入{prepared.shape[2]}×{prepared.shape[1]}，预期{expected_width}×{expected_height}，输出{actual_width}×{actual_height}。')
        ui = dict(result.get('ui', {})) if isinstance(result, dict) else {}
        ui['codex_input_dimensions'] = [input_dimensions(
            original, prepared, factor=kwargs.get('总倍率', 2),
            output_width=actual_width, output_height=actual_height,
            actual_scale_x=actual_width/int(prepared.shape[2]), actual_scale_y=actual_height/int(prepared.shape[1]),
            original_scale_x=actual_width/int(original.shape[2]), original_scale_y=actual_height/int(original.shape[1]))]
        return {'ui': ui, 'result': (image, True)}


class CodexStudioStageOutput(CodexImageOutput):
    @classmethod
    def INPUT_TYPES(cls):
        result = copy.deepcopy(super().INPUT_TYPES())
        result.setdefault('optional', {})['启用输出'] = ('BOOLEAN', {'default': True})
        return result

    def execute(self, 图像, 启用输出=True, **kwargs):
        if not 启用输出:
            return {'ui': {'images': [], 'codex_stage_skipped': [True]}, 'result': (图像, '未启用')}
        return super().execute(图像=图像, **kwargs)


class CodexQwenStudioResults:
    @classmethod
    def INPUT_TYPES(cls):
        return {'required': {'生成图': ('IMAGE',), '修复图': ('IMAGE',), '超分图': ('IMAGE',)},
                'optional': {'参考图集': ('CODEX_REFERENCES',),
                             '修复已启用': ('BOOLEAN', {'default': False}),
                             '超分已启用': ('BOOLEAN', {'default': False})},
                'hidden': {'prompt': 'PROMPT', 'extra_pnginfo': 'EXTRA_PNGINFO', 'unique_id': 'UNIQUE_ID'}}

    RETURN_TYPES = ()
    FUNCTION = 'execute'
    OUTPUT_NODE = True
    CATEGORY = 'Codex/千问工作台'

    def execute(self, 生成图, 修复图, 超分图, 参考图集=None, 修复已启用=False, 超分已启用=False,
                prompt=None, extra_pnginfo=None, unique_id=None):
        def preview(image, name):
            return CodexImageOutput().execute(image, '预览（不保存）', 'Qwen工作台/' + name,
                                              prompt=prompt, extra_pnginfo=extra_pnginfo, unique_id=unique_id)['ui']['images']
        references = []
        collection = 参考图集 or {}
        for index, image in enumerate(collection.get('images', [])):
            descriptor = preview(image, '参考图')[0]
            entries = collection.get('entries', [])
            if index < len(entries):
                descriptor.update({key: entries[index][key] for key in ('id', 'name') if key in entries[index]})
            references.append(descriptor)
        return {'ui': {'generated': preview(生成图, '生成原图'),
                       'repaired': preview(修复图, '修复结果') if 修复已启用 else [],
                       'upscaled': preview(超分图, '最终结果') if 超分已启用 else [],
                       'references': references}, 'result': ()}


NODE_CLASS_MAPPINGS = {cls.__name__: cls for cls in (
    CodexQwenStudioControls, CodexQwenStudioPrompt, CodexQwenStudioRules,
    CodexQwenStudioUpscale, CodexQwenStudioResults, CodexStudioStageOutput)}
NODE_DISPLAY_NAME_MAPPINGS = {
    'CodexQwenStudioControls': '千问工作台 · 统一控制',
    'CodexQwenStudioPrompt': '提示词 · 输入与优化',
    'CodexQwenStudioRules': '前置提示词 · 生图 / 优化 / 修复',
    'CodexQwenStudioUpscale': '千问工作台 · 可选超分',
    'CodexQwenStudioResults': '图像结果 · 生成 / 修复 / 超分',
    'CodexStudioStageOutput': '阶段输出 · 启用时预览与保存',
}
