"""Scene prompts and compact, order-aware reference guidance."""
from __future__ import annotations

from copy import deepcopy

VERSION = "1.1.0"
PRESET_LEVELS = ["简明", "标准", "细致", "丰富", "精细"]

_TEMPLATES = [
    "以@形象中的人物为主体，穿@服装，将同一人物的动作改为@姿态所示，双手位置、手臂弯曲、身体倾斜、双腿开合及视角一致。成年约7～8头身，头部尺寸与全身协调，双手双腿双脚完整、肤色连续，皮肤自然、衣料真实；人数遵循动作参考，统一重打光。{scene}。",
    "以@形象的人物为主体，仅保留五官发型，穿@服装；将同一人物全身动作改为@姿态所示，双手位置、手臂弯曲、身体倾斜、双腿开合及视角一致。成年约7～8头身，双手双腿双脚完整、肤色连续；衣物随动作合身，人数遵循动作参考。{scene}，统一重打光。",
    "以@形象中的人物为主体，仅保留五官发型，穿@服装。将同一人物全身动作改为@姿态所示，双手位置、手臂弯曲、身体倾斜、双腿开合及视角一致。成年约7～8头身，头部尺寸与全身协调，双手双腿双脚完整、肤色连续；衣装只供款式，随动作合身。人数遵循动作参考。{scene}，统一重打光、色温与接触阴影。",
    "以@形象的人物为主体，只锁五官发型，穿@服装，将其全身动作改为@姿态所示；双手位置、手臂弯曲、身体倾斜、双腿开合与取景视角一致，动作参考只传达位置。成年约7～8头身，头部尺寸与全身协调，双手双腿双脚完整、肤色连续，皮肤自然、衣料真实。衣服随动作贴合，人数遵循动作参考。{scene}。按环境统一重打光与接触阴影。",
    "以@形象的人物为主体，仅锁五官发型，穿@服装；将同一人物的全身动作改为@姿态所示，双手位置、手臂弯曲、身体倾斜、双腿开合及取景视角一致，动作参考只作位置指令。成年约7～8头身，头部尺寸与全身协调，双手双腿双脚完整、肤色连续，皮肤自然、衣料真实。服装只供款式配色，随动作贴合；人数与动作关系遵循参考。{scene}。统一重打光与色温，匹配高光、遮挡及接触阴影，输出单幅照片。",
]

# Scene detail grows with the five levels; the body/reference constraints remain.
_SCENES = [
    ("combine", "组合参考", ["简洁真实环境", "简洁真实环境，人物自然融入", "简洁真实环境，人物与空间尺度一致", "简洁真实环境，人物与空间尺度一致，背景层次克制", "简洁真实环境，人物与空间尺度一致，背景留白克制而有层次"]),
    ("portrait", "自然写真", ["自然光人物写真", "自然光人物写真，肤质真实", "自然光人物写真，背景柔和，皮肤纹理真实", "自然光人物写真，窗外散射光柔和，背景细节适度虚化", "自然光人物写真，柔和散射光包裹面部，肤质真实，背景适度虚化"]),
    ("studio", "服装棚拍", ["服装棚拍，背景简洁", "服装棚拍，柔和布光展示衣装", "服装棚拍，中性背景，清楚展示剪裁与材质", "服装棚拍，中性无缝背景，主辅光展示剪裁，面料细节清楚", "服装棚拍，中性无缝背景，柔和主辅光表现剪裁、纹理与衣物体积"]),
    ("solid_studio", "纯色棚拍", ["纯色背景棚拍", "纯色背景棚拍，人物轮廓清楚", "纯色背景棚拍，背景色克制，人物轮廓清楚", "纯色背景棚拍，背景平整，柔和主光与淡淡轮廓光分离人物", "纯色背景棚拍，背景平整克制，柔和主光与淡淡轮廓光呈现人物体积"]),
    ("street", "城市街拍", ["城市街道自然街拍", "城市街道自然街拍，建筑真实", "城市街道自然街拍，建筑与路面尺度真实", "城市街道自然街拍，建筑延伸形成纵深，路面反光与天光协调", "城市街道自然街拍，建筑纵深真实，天光与路面反光协调，保留生活气息"]),
    ("park", "公园漫步", ["公园绿地自然天光", "公园绿地自然天光，树木层次清楚", "公园步道与绿地，树荫下的柔和自然天光", "公园步道与绿地，树荫下散射光柔和，远近植被形成层次", "公园步道与绿地，树荫散射光自然，远近植被分层，人物与地面接触真实"]),
    ("beach", "海边假日", ["海边沙滩自然天光", "海边沙滩自然天光，海天层次真实", "海边沙滩与海面，自然天光，海天背景真实", "海边沙滩与远处海面，海风轻拂衣物，海天保持真实层次", "海边沙滩与远处海面，海风轻拂衣物，自然天光下海天层次与沙地反光真实"]),
    ("mountain", "山间户外", ["山间步道自然天光", "山间步道自然天光，远山有层次", "山间步道与远山，自然天光表现真实空间", "山间步道与远山，薄雾形成空间层次，衣料与岩石质感真实", "山间步道与远山，自然天光穿过薄雾，岩石植被与衣料呈现真实细节"]),
    ("cafe", "咖啡馆", ["咖啡馆靠窗区域", "咖啡馆靠窗区域，柔和窗光", "咖啡馆靠窗区域，柔和窗光与木质陈设协调", "咖啡馆靠窗区域，窗光与室内暖光平衡，木质陈设自然有层次", "咖啡馆靠窗区域，窗光与室内暖光平衡，桌椅尺度真实，背景氛围安静"]),
    ("restaurant", "餐厅用餐", ["餐厅真实用餐环境", "餐厅用餐环境，灯光柔和", "餐厅用餐环境，柔和灯光，桌椅比例真实", "餐厅用餐环境，柔和暖光与餐桌陈设协调，背景有真实纵深", "餐厅用餐环境，暖光柔和，餐桌器皿尺度真实，人物与座椅关系自然"]),
    ("hotel_lobby", "酒店大堂", ["酒店大堂真实空间", "酒店大堂，空间尺度真实", "酒店大堂，窗光与室内照明协调，空间开阔", "酒店大堂，窗光与室内照明协调，石材与座椅构成开阔纵深", "酒店大堂，窗光与室内照明协调，石材反射克制，空间尺度与人物一致"]),
    ("hotel_room", "酒店客房", ["酒店客房柔和窗光", "酒店客房，柔和窗光与整洁陈设", "酒店客房，柔和窗光，床品与窗帘质感真实", "酒店客房，柔和窗光与床头灯协调，床品窗帘质感自然", "酒店客房，柔和窗光与床头灯协调，床品有自然褶皱，私密氛围舒适真实"]),
    ("home", "居家生活", ["居家客厅自然光", "居家客厅自然光，陈设亲切", "居家客厅自然光，家具尺度与生活陈设真实", "居家客厅，柔和窗光照亮家具与生活陈设，环境松弛自然", "居家客厅，柔和窗光照亮家具与生活陈设，纹理克制，呈现松弛日常氛围"]),
    ("office", "办公空间", ["现代办公空间", "现代办公空间，光线明净", "现代办公空间，窗光与顶灯协调，桌椅真实", "现代办公空间，窗光与顶灯协调，办公桌椅排列形成清楚纵深", "现代办公空间，窗光与顶灯协调，桌椅尺度准确，背景整洁但保留工作气息"]),
    ("cinema", "影院氛围", ["影院休息区真实环境", "影院休息区，低亮度环境照明", "影院休息区，低亮度灯光与走廊空间协调", "影院休息区，低亮度环境光与导向灯协调，座椅与走廊层次真实", "影院休息区，环境灯与导向灯保持低亮度，人物面部可辨，座椅走廊纵深真实"]),
    ("amusement_park", "游乐园", ["游乐园户外环境", "游乐园户外环境，色彩明快", "游乐园户外环境，色彩明快，设施尺度真实", "游乐园户外环境，设施与人行步道构成背景，明快色彩保持自然", "游乐园户外环境，设施与步道有真实尺度，色彩明快克制，人物融入现场光线"]),
    ("stage", "舞台现场", ["舞台现场真实灯光", "舞台现场，人物受现场灯照明", "舞台现场，定向舞台灯与暗部层次真实", "舞台现场，定向灯照亮人物，背景灯与暗部保留现场空间层次", "舞台现场，定向灯与环境补光协调，保留皮肤衣料细节与真实舞台空间"]),
    ("product_display", "产品陈列", ["产品陈列区人物展示", "产品陈列区，人物与商品尺度真实", "产品陈列区，人物与商品尺度真实，主次清楚", "产品陈列区，人物与展台商品尺度真实，陈列有序，灯光突出主体", "产品陈列区，人物与展台商品比例真实，陈列有序，材质反光克制，主次清楚"]),
    ("museum", "展馆参观", ["展馆真实空间", "展馆空间，灯光安静柔和", "展馆空间，展墙与通道尺度真实，灯光柔和", "展馆空间，展墙与通道形成纵深，柔和照明保留安静参观氛围", "展馆空间，展墙通道尺度真实，柔和照明分离人物与展品，保持安静氛围"]),
    ("garden", "庭院花园", ["庭院花园自然天光", "庭院花园，花木与自然天光协调", "庭院花园，花木围合步道，自然天光柔和", "庭院花园，花木围合步道，叶隙天光柔和，石径与衣料质感自然", "庭院花园，花木步道远近分层，叶隙天光柔和，石径与衣料细节自然真实"]),
    ("subway", "地铁出行", ["地铁站公共空间", "地铁站公共空间，站台尺度真实", "地铁站公共空间，顶灯均匀，站台尺度真实", "地铁站公共空间，顶灯与列车反光协调，站台纵深真实，环境整洁", "地铁站公共空间，顶灯与列车反光协调，站台尺度准确，保留真实出行氛围"]),
]


def _defaults():
    result = []
    for identifier, name, scenes in _SCENES:
        variants = [template.format(scene=scene) for template, scene in zip(_TEMPLATES, scenes)]
        result.append({"id": identifier, "name": name, "prompt": variants[2],
                       "variants": variants, "scene": name, "version": VERSION})
    result.append({"id": "custom", "name": "自由描述", "prompt": "", "variants": [""] * 5,
                   "scene": "自由描述", "version": VERSION})
    return result


DEFAULT_PRESETS = _defaults()
_LEGACY = {
    "combine": ("组合参考", "结合所选参考图生成一张完整的人物照片。保留@形象的面部特征，采用@服装的款式与配色，参考@姿态的身体动作。画面自然协调，主体完整，细节清晰。"),
    "portrait": ("人物写真", "根据@形象保持人物身份，参考@服装搭配与@姿态动作，生成自然光下的人物写真，面部清晰，真实皮肤质感，简洁背景。"),
    "studio": ("棚拍展示", "以@形象为人物主体，穿着@服装，采用@姿态。生成服装棚拍展示，柔和均匀布光，纯净背景，完整呈现服装剪裁。"),
    "custom": ("自由描述", ""),
}


def preset_items(user_items=None):
    """Merge defaults; migrate only exact known legacy rows, never edited text."""
    defaults = deepcopy(DEFAULT_PRESETS)
    by_id = {item["id"]: index for index, item in enumerate(defaults)}
    if not isinstance(user_items, list):
        return defaults
    seen = set()
    for original in user_items:
        if not isinstance(original, dict) or not all(isinstance(original.get(key), str) for key in ("id", "name", "prompt")):
            continue
        identifier = original["id"]
        if not identifier or identifier in seen:
            continue
        seen.add(identifier)
        legacy = _LEGACY.get(identifier)
        if legacy == (original["name"], original["prompt"]) and not original.get("variants"):
            continue
        current = defaults[by_id[identifier]] if identifier in by_id else None
        if current and (original["name"], original["prompt"]) == (current["name"], current["prompt"]) and not original.get("variants"):
            continue
        item = deepcopy(original)
        # Old storage only saved id/name/prompt. Repeat a custom text rather than
        # silently injecting built-in levels into the user's edited preset.
        variants = item.get("variants")
        if not isinstance(variants, list) or len(variants) != 5 or not all(isinstance(text, str) for text in variants):
            item["variants"] = [item["prompt"]] * 5
        item.setdefault("scene", item["name"])
        item.setdefault("version", VERSION)
        if identifier in by_id:
            defaults[by_id[identifier]] = item
        else:
            by_id[identifier] = len(defaults)
            defaults.append(item)
    return defaults


def build_role_guidance(payload, asset_items):
    """Return one short prefix; do not rewrite the user's prompt or role order.

    Explicit asset_roles bind by stable ID (or legacy one-based index). Missing
    roles may be inferred only when exactly one selected asset has that kind.
    The caller appends this once before token conversion in reference_prompt.
    """
    identifiers = payload.get("asset_ids", [])
    if not isinstance(identifiers, list) or not identifiers:
        return ""
    if isinstance(asset_items, dict):
        items = {key: value for key, value in asset_items.items() if isinstance(value, dict)}
    else:
        items = {item["id"]: item for item in (asset_items or []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
    explicit = payload.get("asset_roles") if isinstance(payload.get("asset_roles"), dict) else {}
    role_kinds = {"形象": "identity", "服装": "outfit", "姿态": "pose", "场景": None, "道具": None}
    bindings = {}
    for role, kind in role_kinds.items():
        value = explicit.get(role, explicit.get("角色") if role == "形象" else None)
        if type(value) is int:
            value = identifiers[value - 1] if 1 <= value <= len(identifiers) else None
        if isinstance(value, str) and value in identifiers:
            bindings[role] = identifiers.index(value) + 1
        elif value is None and kind is not None:
            candidates = [index for index, identifier in enumerate(identifiers, 1) if items.get(identifier, {}).get("kind") == kind]
            if len(candidates) == 1:
                bindings[role] = candidates[0]
    if not bindings:
        return ""
    text = payload.get("prompt", "")
    text = text if isinstance(text, str) else ""
    label = lambda role: f"参考图{bindings[role]}"
    pose = items.get(identifiers[bindings["姿态"] - 1], {}) if "姿态" in bindings else {}
    metadata = pose.get("metadata") if isinstance(pose.get("metadata"), dict) else {}
    series = metadata.get("series")
    solo = series in {"full_sexy", "full_std", "upper"}
    duo = series == "duo"
    instructions = []
    if "形象" in bindings and "姿态" in bindings:
        if duo:
            instructions.append(f"以{label('形象')}为人物形象参考，采用{label('姿态')}所示双人动作关系")
        else:
            instructions.append(f"将{label('形象')}中的人物改为{label('姿态')}所示动作")
        if "服装" in bindings:
            instructions[-1] += f"并穿{label('服装')}衣装"
    else:
        if "形象" in bindings:
            instructions.append(f"以{label('形象')}的人物为主体")
        if "服装" in bindings:
            instructions.append(f"主体穿{label('服装')}衣装")
        if "姿态" in bindings:
            instructions.append(f"主体动作改为{label('姿态')}所示")
    for role in ("场景", "道具"):
        if role in bindings:
            instructions.append(f"{role}采用{label(role)}")
    pose_names = metadata.get("names") if isinstance(metadata.get("names"), dict) else {}
    angle_names = metadata.get("angle_names") if isinstance(metadata.get("angle_names"), dict) else {}
    localized = metadata.get("localized_record") if isinstance(metadata.get("localized_record"), dict) else {}
    action = pose_names.get("zh") or localized.get("姿势名称") or pose.get("prompt")
    angle = angle_names.get("zh") or localized.get("角度")
    if isinstance(action, str) and action.strip():
        target = action.strip()[:100]
        if isinstance(angle, str) and angle.strip():
            target += "，" + angle.strip()[:20]
        instructions.append("目标动作：" + target)
    if solo:
        instructions.append("最终只有同一位完整真人，动作参考仅作位置指令")
    elif duo:
        instructions.append("人数及互动位置遵循双人动作参考，统一生成完整真人")
    constraints = []
    if "形象" in bindings and not ("五官" in text and "发型" in text and ("头像比例" in text or "头像裁剪" in text) and "光照背景" in text):
        if not ("五官" in text and "发型" in text and "同一人物" in text):
            constraints.append("形象仅锁五官发型，按目标场景重新安排取景和光照")
    if any(role in bindings for role in ("形象", "服装", "姿态")) and not ("7～8头身" in text and "头部尺寸" in text):
        constraints.append("成年约7～8头身，头部尺寸与全身协调")
    if "服装" in bindings and not ("双手" in text and "双腿" in text and "双脚" in text and "肤色连续" in text and ("贴合" in text or "合身" in text)):
        constraints.append("衣物随动作合身，双手双腿双脚完整、肤色连续，皮肤自然、衣料真实")
    if "姿态" in bindings and not all(word in text for word in ("双手位置", "手臂弯曲", "身体倾斜", "双腿开合", "视角")):
        constraints.append("双手位置、手臂弯曲、身体倾斜、双腿开合及视角与动作参考一致")
    if constraints and not any(word in text for word in ("重打光", "重新布光", "统一光照")):
        constraints.append("全场景统一重打光")
    return "；".join(instructions + constraints) + "。"
