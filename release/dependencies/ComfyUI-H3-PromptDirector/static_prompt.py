"""Shared static image language; never carries speech or camera movement."""
import re

DESCRIPTION_RULE = ('只用中文描述参考图中可见的事实。按人物/物体、姿态动作、服装、画面左右位置、前中后景、机位、光线、色彩和材质组织。'
                    '区分画面左/右和人物自身左/右，不能镜像翻转。看不清就省略，不臆造被遮住的身体或细节。'
                    '保留人物可见的体态比例、服装轮廓和真实材质，不夸大、不擅改年龄与身份。'
                    '只输出可以复现该静态画面的描述，不输出分析、对白、说话者编号、视频时长或运动镜头指令。')
REPAIR_RULE = ('仅修复可见的不合理五官、手指、脚部、肢体比例、接触遮挡和衣物破损或接缝错误。'
               '保留同一人物身份、脸型、正常身体结构、原姿态、服装覆盖、构图、场景、光照及自然肤质。'
               '没有明确错误的位置维持原样，不改变服装覆盖，不增加人物或物件，不磨皮，不添加文字。')


def static_text(value):
    value=re.sub(r'<d\b[^>]*>.*?</d>', '', str(value or ''), flags=re.S|re.I)
    value=re.sub(r'[（(\[]?(?<![A-Za-z0-9])S[1-9]\d*(?![A-Za-z0-9])[）)\]]?', '', value)
    value=re.sub(r'</?(?:Subject|Picture|Audio|Video)\b[^>]*>', '', value, flags=re.I)
    parts=re.split(r'(?<=[。；;\n])',value)
    return ''.join(part for part in parts if not re.search(
        r'对焦呼吸|曝光呼吸|手持晃|镜头(?:缓慢|慢慢|持续|逐渐)?(?:推进|拉远|移动|环绕)|慢动作|连续镜头|不切镜|\b(?:fps|lip.sync|speaker_id)\b',part,re.I)).strip()
