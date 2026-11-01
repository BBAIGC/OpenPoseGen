"""Editable semantic rules; structural JSON and dialogue contracts stay in code."""
from __future__ import annotations

import re

DEFAULT_PROMPT_RULES = {
    "planning": "通读完整原稿，按事件先后和各镜时长拆分镜头；每镜只安排本镜的新事件，不把整篇剧情重复到每一镜。对白与触发它的动作一起安排，原句少于镜头数时，将有对白的剧情节点自然分布到开头、中段和后段，避免前面连续说完、后面长时间空镜；原稿明确的静默、时间点和剧情因果优先，不机械平均、不新增或重复台词。静默镜头有明确叙事作用，不为填满时长擅加人物、剧情或镜头动作。",
    "environment": "环境、光线和色彩以主参考图为准。未明确要求改变的部分保持原样，不额外补写光效、色调或曝光；后镜只继承前镜的动作状态，画面色彩始终参考原图。",
    "state": "人物身份连续，衣物、姿态、表情、手持物及道具位置随剧情更新。后镜从前镜结束状态继续，未改变的实体逐项保留，已完成事件保持其结果，不恢复初始状态再做一遍。姿态变化只按原稿真实发生；不能因为动作元数据遗漏就把明确的起身、坐下或休息姿态锁回旧姿态。每镜聚焦一个主要动作，原稿要求的次要动作按顺序轻柔衔接；手部接触写清哪只手、接触位置和前后关系，避免多只手同时交叉或道具穿插，不为丰富画面额外加大动作。",
    "references": "保留每张素材图的独立来源和角色；同一镜可把不同素材中的人物或物体按原稿组合，不能把编号互换或只看第一张。原图提供稳定身份、皮肤纹理和材质细节，上一分镜只补几何和状态连续性，不能把上一分镜当成新的清晰度或磨皮标准。独立规划每镜静态构图、景别和视角；在原稿允许的镜头语言内体现眼神、表情、手的位置和身体朝向变化，构图不能恢复旧衣着或未来动作。连续长镜头保持同一机位移动轨迹，不凭空切换景别或角度。",
    "video": "用简洁中文描述画面、当前状态、本镜新动作、表情和镜头运动。逐字对白只从指定白名单回填，声音属于对应的可见说话人，发声时自然开口；不擅加背景人声。保留原有环境音和配乐选择，跨镜继承已完成状态。",
    "image": "先根据剧情中已完成的事件落实本镜开场状态：例如帽子已摘下放在桌上、外套已挂好、杯子在手中或已放回，分别写清穿戴层次、物件位置与持有状态；未发生的变化不要提前表现。生成这个开场瞬间的一张静态图，再应用该镜景别、机位和布局。原素材只提供身份、材质、环境与细节，不恢复过期状态。保持原图自然皮肤纹理、发丝及衣料细节，不逐镜磨皮。手与物体接触边界、遮挡关系和人体比例自然，不表现多次手势，不写S编号、对白、帧率、镜头移动或晃动，不拼图或添加字幕。",
}
RULE_LABELS = {"planning": "分镜规划规则", "environment": "环境与光照规则", "state": "动作与状态规则",
               "references": "多图与构图规则", "video": "视频提示词规则", "image": "生图提示词规则"}


def normalize_prompt_rules(rules=None):
    if rules is None:
        return dict(DEFAULT_PROMPT_RULES)
    if not isinstance(rules, dict) or set(rules) - set(DEFAULT_PROMPT_RULES):
        raise ValueError("提示词规则配置无效。")
    result = dict(DEFAULT_PROMPT_RULES)
    for key, value in rules.items():
        if not isinstance(value, str) or not value.strip() or len(value) > 12000:
            raise ValueError(f"{RULE_LABELS[key]}应为1～12000字。")
        result[key] = value.strip()
    if sum(len(value) for value in result.values()) > 36000:
        raise ValueError("六类提示词规则总长度不能超过36000字。")
    return result


def preset_system_text(rules=None):
    values = normalize_prompt_rules(rules)
    return "\n\n".join(f"{RULE_LABELS[key]}：\n{values[key]}" for key in DEFAULT_PROMPT_RULES)


def prompt_rule_text(rules, stage):
    if stage not in ("planning", "environment", "state", "references", "video", "image"):
        raise ValueError("未知提示词规则类别。")
    return normalize_prompt_rules(rules)[stage]


def apply_stage_rules(prompt, rules, stage):
    if rules is None:
        return prompt
    if stage not in ("image", "video"):
        raise ValueError("未知提示词执行阶段。")
    label = "生图" if stage == "image" else "视频"
    marker = f"【{label}执行规则】"
    body = re.sub(re.escape(marker) + r".*?【执行规则结束】", "", str(prompt), flags=re.S).strip()
    rule = prompt_rule_text(rules, stage)
    # These defaults already govern planning. Repeating planner instructions
    # in a diffusion prompt risks turning them into audible narration.
    if rule == DEFAULT_PROMPT_RULES[stage]:
        return body
    clauses = re.split(r"(?<=[。！？\n])", rule)
    rule = "".join(clause for clause in clauses if not re.search(
        r"白名单|回填|原稿|提示词|分镜规划|执行规则|控制标签|JSON|覆盖.*条件|schema|</?d\b|(?:旁白|对白|台词|说)\s*[:：]", clause, re.I)).strip()
    if not rule:
        return body
    if rule in body:
        return body
    block = rule if stage == "video" else marker + rule + "【执行规则结束】"
    if stage == "video" and "overall_soundscape:" in body:
        position = body.index("overall_soundscape:")
        return body[:position].rstrip() + "\n" + block + "\n\n" + body[position:]
    return body + "\n" + block


class H3PromptRules:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {RULE_LABELS[key]: ("STRING", {"default": value, "multiline": True})
                             for key, value in DEFAULT_PROMPT_RULES.items()}}

    RETURN_TYPES = ("H3_PROMPT_RULES",)
    RETURN_NAMES = ("提示词规则",)
    FUNCTION = "execute"
    CATEGORY = "H3/模型与采样"
    DESCRIPTION = "查看和编辑规划、环境、状态、多图、视频与生图规则；连接工作台后生效，节点不加载模型。"

    def execute(self, **kwargs):
        return (normalize_prompt_rules({key: kwargs[RULE_LABELS[key]] for key in DEFAULT_PROMPT_RULES}),)


PRESET_NODE_CLASS_MAPPINGS = {"H3PromptRules": H3PromptRules}
PRESET_NODE_DISPLAY_NAME_MAPPINGS = {"H3PromptRules": "分镜提示词规则"}
