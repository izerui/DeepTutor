"""The built-in classification (platform direction §6, appendix A).

Stages, two levels of subjects under each stage, and difficulties. Every
node has a fixed id: ``<stage>``, ``<stage>.<subject>``,
``<stage>.<subject>.<child>``. Names come in Chinese and English; the other
interface languages show the English one.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

# (key, zh, en, children) — children are (key, zh, en).
_K12_PRIMARY = [
    ("chinese", "语文", "Chinese"), ("math", "数学", "Mathematics"), ("english", "英语", "English"),
    ("science", "科学", "Science"), ("morality_law", "道德与法治", "Morality and Law"),
    ("it", "信息科技", "Information Technology"), ("music", "音乐", "Music"), ("art", "美术", "Art"),
    ("pe_health", "体育与健康", "PE and Health"), ("labor", "劳动", "Labor Education"),
]
_K12_JUNIOR = [
    ("chinese", "语文", "Chinese"), ("math", "数学", "Mathematics"), ("english", "英语", "English"),
    ("physics", "物理", "Physics"), ("chemistry", "化学", "Chemistry"), ("biology", "生物学", "Biology"),
    ("history", "历史", "History"), ("geography", "地理", "Geography"),
    ("morality_law", "道德与法治", "Morality and Law"), ("it", "信息科技", "Information Technology"),
    ("music", "音乐", "Music"), ("art", "美术", "Art"), ("pe_health", "体育与健康", "PE and Health"),
]
_K12_SENIOR = [
    ("chinese", "语文", "Chinese"), ("math", "数学", "Mathematics"), ("english", "英语", "English"),
    ("physics", "物理", "Physics"), ("chemistry", "化学", "Chemistry"), ("biology", "生物学", "Biology"),
    ("history", "历史", "History"), ("geography", "地理", "Geography"),
    ("politics", "思想政治", "Politics"), ("it", "信息技术", "Information Technology"),
    ("general_tech", "通用技术", "General Technology"), ("music", "音乐", "Music"), ("art", "美术", "Art"),
    ("pe_health", "体育与健康", "PE and Health"),
]

_UNIVERSITY: list[tuple[str, str, str, list[tuple[str, str, str]]]] = [
    ("foundation", "公共基础课", "Foundation courses", [
        ("calculus", "高等数学", "Calculus"), ("linear_algebra", "线性代数", "Linear algebra"),
        ("probability", "概率论与数理统计", "Probability and statistics"),
        ("physics", "大学物理", "University physics"), ("english", "大学英语", "College English"),
        ("computing", "大学计算机", "Computing fundamentals"),
        ("politics", "思想政治理论", "Ideological and political theory"), ("pe", "体育", "Physical education"),
        ("career", "职业规划与创新创业", "Career planning and entrepreneurship"),
    ]),
    ("philosophy", "哲学", "Philosophy", [("philosophy", "哲学类", "Philosophy")]),
    ("economics", "经济学", "Economics", [
        ("economics", "经济学类", "Economics"), ("public_finance", "财政学类", "Public finance"),
        ("finance", "金融学类", "Finance"), ("trade", "经济与贸易类", "Economics and trade"),
    ]),
    ("law", "法学", "Law", [
        ("law", "法学类", "Law"), ("political_science", "政治学类", "Political science"),
        ("sociology", "社会学类", "Sociology"), ("ethnology", "民族学类", "Ethnology"),
        ("marxism", "马克思主义理论类", "Marxist theory"), ("public_security", "公安学类", "Public security"),
    ]),
    ("education", "教育学", "Education", [
        ("education", "教育学类", "Education"), ("sports", "体育学类", "Sports science"),
    ]),
    ("literature", "文学", "Literature", [
        ("chinese", "中国语言文学类", "Chinese language and literature"),
        ("foreign", "外国语言文学类", "Foreign languages and literature"),
        ("journalism", "新闻传播学类", "Journalism and communication"),
    ]),
    ("history", "历史学", "History", [("history", "历史学类", "History")]),
    ("science", "理学", "Science", [
        ("math", "数学类", "Mathematics"), ("physics", "物理学类", "Physics"), ("chemistry", "化学类", "Chemistry"),
        ("astronomy", "天文学类", "Astronomy"), ("geography", "地理科学类", "Geography"),
        ("atmosphere", "大气科学类", "Atmospheric science"), ("marine", "海洋科学类", "Marine science"),
        ("geophysics", "地球物理学类", "Geophysics"), ("geology", "地质学类", "Geology"),
        ("biology", "生物科学类", "Biological sciences"), ("psychology", "心理学类", "Psychology"),
        ("statistics", "统计学类", "Statistics"),
    ]),
    ("engineering", "工学", "Engineering", [
        ("mechanics", "力学类", "Mechanics"), ("mechanical", "机械类", "Mechanical engineering"),
        ("instrument", "仪器类", "Instrumentation"), ("materials", "材料类", "Materials"),
        ("energy", "能源动力类", "Energy and power"), ("electrical", "电气类", "Electrical engineering"),
        ("electronics", "电子信息类", "Electronics and information"), ("automation", "自动化类", "Automation"),
        ("computer", "计算机类", "Computer science"), ("civil", "土木类", "Civil engineering"),
        ("water", "水利类", "Water resources"), ("surveying", "测绘类", "Surveying and mapping"),
        ("chemical", "化工与制药类", "Chemical and pharmaceutical engineering"),
        ("geological", "地质类", "Geological engineering"), ("mining", "矿业类", "Mining"),
        ("textile", "纺织类", "Textiles"), ("light_industry", "轻工类", "Light industry"),
        ("transportation", "交通运输类", "Transportation"), ("ocean", "海洋工程类", "Ocean engineering"),
        ("aerospace", "航空航天类", "Aerospace"), ("weapons", "兵器类", "Weapons"),
        ("nuclear", "核工程类", "Nuclear engineering"), ("agricultural", "农业工程类", "Agricultural engineering"),
        ("forestry", "林业工程类", "Forestry engineering"),
        ("environment", "环境科学与工程类", "Environmental science and engineering"),
        ("biomedical", "生物医学工程类", "Biomedical engineering"),
        ("food", "食品科学与工程类", "Food science and engineering"), ("architecture", "建筑类", "Architecture"),
        ("safety", "安全科学与工程类", "Safety engineering"), ("bioengineering", "生物工程类", "Bioengineering"),
        ("police_tech", "公安技术类", "Public security technology"),
        ("interdisciplinary", "交叉工程类", "Interdisciplinary engineering"),
    ]),
    ("agriculture", "农学", "Agriculture", [
        ("plant", "植物生产类", "Plant production"), ("ecology", "自然保护与环境生态类", "Nature conservation and ecology"),
        ("animal", "动物生产类", "Animal production"), ("veterinary", "动物医学类", "Veterinary medicine"),
        ("forestry", "林学类", "Forestry"), ("fisheries", "水产类", "Fisheries"), ("grassland", "草学类", "Grassland science"),
    ]),
    ("medicine", "医学", "Medicine", [
        ("basic", "基础医学类", "Basic medicine"), ("clinical", "临床医学类", "Clinical medicine"),
        ("stomatology", "口腔医学类", "Stomatology"), ("public_health", "公共卫生与预防医学类", "Public health"),
        ("tcm", "中医学类", "Traditional Chinese medicine"),
        ("integrated", "中西医结合类", "Integrated Chinese and Western medicine"),
        ("pharmacy", "药学类", "Pharmacy"), ("chinese_pharmacy", "中药学类", "Chinese pharmacy"),
        ("forensic", "法医学类", "Forensic medicine"), ("technology", "医学技术类", "Medical technology"),
        ("nursing", "护理学类", "Nursing"),
    ]),
    ("management", "管理学", "Management", [
        ("science_engineering", "管理科学与工程类", "Management science and engineering"),
        ("business", "工商管理类", "Business administration"),
        ("agri_economics", "农业经济管理类", "Agricultural economics and management"),
        ("public", "公共管理类", "Public administration"),
        ("library", "图书情报与档案管理类", "Library, information and archives"),
        ("logistics", "物流管理与工程类", "Logistics"), ("industrial", "工业工程类", "Industrial engineering"),
        ("ecommerce", "电子商务类", "E-commerce"), ("tourism", "旅游管理类", "Tourism management"),
    ]),
    ("arts", "艺术学", "Arts", [
        ("theory", "艺术学理论类", "Art theory"), ("music_dance", "音乐与舞蹈学类", "Music and dance"),
        ("drama_film", "戏剧与影视学类", "Drama, film and television"), ("fine_arts", "美术学类", "Fine arts"),
        ("design", "设计学类", "Design"),
    ]),
]

_POSTGRAD = [
    ("public", "公共课", "Common subjects", [
        ("politics", "思想政治理论", "Politics"), ("english1", "英语一", "English I"), ("english2", "英语二", "English II"),
        ("math1", "数学一", "Mathematics I"), ("math2", "数学二", "Mathematics II"), ("math3", "数学三", "Mathematics III"),
        ("management", "管理类联考综合", "Management joint exam"), ("economics", "经济类联考综合", "Economics joint exam"),
    ]),
    ("major", "专业课", "Major subjects", [
        (key, zh, en) for key, zh, en, _ in _UNIVERSITY if key != "foundation"
    ]),
]

_VOCATIONAL = [
    ("it", "IT 与互联网", "IT and internet", [
        ("programming", "编程语言", "Programming languages"), ("frontend", "前端开发", "Front-end development"),
        ("backend", "后端开发", "Back-end development"), ("mobile", "移动开发", "Mobile development"),
        ("data", "数据分析", "Data analysis"), ("ai", "人工智能", "Artificial intelligence"),
        ("security", "网络与安全", "Networks and security"), ("ops", "运维与云计算", "Operations and cloud"),
        ("testing", "软件测试", "Software testing"),
    ]),
    ("certificates", "职业资格证书", "Professional certificates", [
        ("teacher", "教师资格", "Teacher qualification"), ("accounting", "会计财税", "Accounting and tax"),
        ("civil_service", "公务员与事业单位", "Civil service"), ("legal", "法律职业资格", "Legal profession"),
        ("computer_rank", "计算机等级", "Computer rank exams"), ("construction", "工程建设", "Construction"),
        ("health", "医药卫生", "Health care"), ("finance", "金融证券", "Finance and securities"),
    ]),
    ("office", "办公效率", "Office productivity", [
        ("software", "办公软件", "Office software"), ("writing", "写作与表达", "Writing and speaking"),
        ("time", "时间管理", "Time management"),
    ]),
    ("design", "设计创作", "Design and creation", [
        ("graphic", "平面设计", "Graphic design"), ("ui", "UI 设计", "UI design"),
        ("video", "视频剪辑", "Video editing"), ("3d", "三维与动画", "3D and animation"),
    ]),
    ("management", "经营管理", "Business management", [
        ("project", "项目管理", "Project management"), ("hr", "人力资源", "Human resources"),
        ("startup", "创业", "Entrepreneurship"),
    ]),
    ("marketing", "市场营销", "Marketing", [
        ("new_media", "新媒体运营", "New media operations"), ("ecommerce", "电商运营", "E-commerce operations"),
        ("brand", "品牌与广告", "Brand and advertising"),
    ]),
]

_INTEREST = [
    ("languages", "外语", "Foreign languages"), ("reading", "文学阅读", "Literature and reading"),
    ("humanities", "历史人文", "History and humanities"), ("art_appreciation", "艺术欣赏", "Art appreciation"),
    ("music", "音乐", "Music"), ("calligraphy_photo", "书画摄影", "Calligraphy, painting and photography"),
    ("fitness", "健康运动", "Health and fitness"), ("psychology", "心理成长", "Personal growth"),
    ("popular_science", "科普", "Popular science"), ("finance", "理财", "Personal finance"),
]

_STAGES: list[tuple[str, str, str, list]] = [
    ("primary", "小学", "Primary school", [(k, zh, en, []) for k, zh, en in _K12_PRIMARY]),
    ("junior", "初中", "Junior high school", [(k, zh, en, []) for k, zh, en in _K12_JUNIOR]),
    ("senior", "高中", "Senior high school", [(k, zh, en, []) for k, zh, en in _K12_SENIOR]),
    ("university", "大学", "University", _UNIVERSITY),
    ("postgrad_exam", "考研", "Postgraduate entrance exam", _POSTGRAD),
    ("vocational", "职业技能", "Vocational skills", _VOCATIONAL),
    ("interest", "兴趣与生活", "Interests and life", [(k, zh, en, []) for k, zh, en in _INTEREST]),
]

DIFFICULTIES: list[tuple[str, str, str]] = [
    ("beginner", "入门", "Beginner"),
    ("elementary", "初级", "Elementary"),
    ("intermediate", "中级", "Intermediate"),
    ("advanced", "高级", "Advanced"),
]

MAX_STAGES = 3
MAX_SUBJECTS = 3
MAX_GRADES = 3

_CN_GRADES = ["一年级", "二年级", "三年级", "四年级", "五年级", "六年级", "七年级", "八年级", "九年级", "高一", "高二", "高三"]
#: Primary and secondary school grades (follow-ups §2): stage -> grade numbers.
GRADE_STAGES: dict[str, tuple[int, ...]] = {"primary": (1, 2, 3, 4, 5, 6), "junior": (7, 8, 9), "senior": (10, 11, 12)}


def _grade_names(number: int) -> dict[str, str]:
    french = "1re année" if number == 1 else f"{number}e année"
    return {"zh": _CN_GRADES[number - 1], "en": f"Grade {number}", "de": f"{number}. Klasse", "fr": french,
            "pl": f"Klasa {number}", "uk": f"{number} клас"}


def grades() -> list[dict[str, Any]]:
    return [
        {"id": f"grade.{number}", "stage": stage, "names": _grade_names(number)}
        for stage, numbers in GRADE_STAGES.items() for number in numbers
    ]


def grade_stage(grade_id: str) -> str | None:
    return next((g["stage"] for g in grades() if g["id"] == grade_id), None)


@lru_cache(maxsize=1)
def _preset() -> dict[str, dict[str, Any]]:
    """The built-in nodes: id -> {parent, names, position}."""
    from deeptutor.catalog.taxonomy_i18n import localized

    out: dict[str, dict[str, Any]] = {}
    for s_index, (key, zh, en, subjects) in enumerate(_STAGES):
        out[key] = {"parent": "", "names": localized({"zh": zh, "en": en}), "position": s_index * 10.0}
        for j, (sub, szh, sen, children) in enumerate(subjects):
            sid = f"{key}.{sub}"
            out[sid] = {"parent": key, "names": localized({"zh": szh, "en": sen}), "position": j * 10.0}
            for k, (child, czh, cen) in enumerate(children):
                out[f"{sid}.{child}"] = {
                    "parent": sid, "names": localized({"zh": czh, "en": cen}), "position": k * 10.0,
                }
    return out


_merged_cache: dict[str, Any] = {}


def _signature() -> tuple[str, int]:
    from deeptutor.catalog.store import db

    path = db.path()
    try:
        return str(path), path.stat().st_mtime_ns
    except OSError:
        return str(path), 0


def _nodes() -> dict[str, dict[str, Any]]:
    """Built-in nodes with administrators' changes, plus their own entries.

    Each: {parent, names, position, hidden, custom}. Re-read when the store changes.
    """
    signature = _signature()
    if _merged_cache.get("signature") == signature:
        return _merged_cache["nodes"]
    from deeptutor.catalog.store import db

    nodes = {
        node_id: {**value, "names": dict(value["names"]), "hidden": False, "custom": False}
        for node_id, value in _preset().items()
    }
    try:
        with db.read() as conn:
            rows = conn.execute("SELECT * FROM taxonomy_nodes").fetchall() if conn else []
    except Exception:
        rows = []  # an unreadable store shows the built-in classification
    import json

    def others(row) -> dict[str, str]:
        try:
            value = json.loads(row["names_json"] if "names_json" in row.keys() else "{}") or {}
        except ValueError:
            value = {}
        if not isinstance(value, dict):
            value = {}
        return {k: str(v) for k, v in value.items() if k in OTHER_LANGUAGES and str(v).strip()}

    for row in rows:
        if row["custom"]:
            nodes[row["node_id"]] = {
                "parent": row["parent"], "names": {"zh": row["name_zh"], "en": row["name_en"], **others(row)},
                "position": row["position"] if row["position"] is not None else 1e6,
                "hidden": bool(row["hidden"]), "custom": True,
            }
            continue
        found = nodes.get(row["node_id"])
        if found is None:
            continue
        if row["name_zh"] or row["name_en"]:
            # Renamed by an administrator: the built-in translations no longer match,
            # unless the administrator gave them too.
            found["names"] = {
                "zh": row["name_zh"] or found["names"]["zh"], "en": row["name_en"] or found["names"]["en"],
            }
        found["names"].update(others(row))
        found["hidden"] = bool(row["hidden"])
        if row["position"] is not None:
            found["position"] = row["position"]
    # An entry under a node that is gone has nowhere to show.
    nodes = {k: v for k, v in nodes.items() if not v["parent"] or v["parent"] in nodes}
    _merged_cache.update(signature=signature, nodes=nodes)
    return nodes


def _children(nodes: dict[str, dict[str, Any]], parent: str, include_hidden: bool) -> list[str]:
    return sorted(
        (k for k, v in nodes.items() if v["parent"] == parent and (include_hidden or not v["hidden"])),
        key=lambda k: (nodes[k]["position"], k),
    )


def tree(*, include_hidden: bool = False) -> dict[str, Any]:
    """The classification as the client receives it; *include_hidden* for administrators."""
    nodes = _nodes()

    def shape(node_id: str) -> dict[str, Any]:
        out: dict[str, Any] = {"id": node_id, "names": nodes[node_id]["names"]}
        if include_hidden:
            out["hidden"] = nodes[node_id]["hidden"]
            out["custom"] = nodes[node_id]["custom"]
        return out

    stages = []
    for stage in _children(nodes, "", include_hidden):
        subjects = []
        for subject in _children(nodes, stage, include_hidden):
            subjects.append({
                **shape(subject),
                "children": [shape(child) for child in _children(nodes, subject, include_hidden)],
            })
        stages.append({**shape(stage), "subjects": subjects})
    from deeptutor.catalog.taxonomy_i18n import localized

    return {
        "stages": stages,
        "grades": grades(),
        "difficulties": [{"id": key, "names": localized({"zh": zh, "en": en})} for key, zh, en in DIFFICULTIES],
    }


def _index() -> dict[str, dict[str, Any]]:
    """Every node id, hidden ones too -> {kind, stage, parent, names, hidden}."""
    nodes = _nodes()
    out: dict[str, dict[str, Any]] = {}
    for node_id, value in nodes.items():
        chain = [node_id]
        while nodes[chain[-1]]["parent"]:
            chain.append(nodes[chain[-1]]["parent"])
        stage = chain[-1]
        out[node_id] = {
            "kind": "stage" if node_id == stage else "subject",
            "stage": stage,
            "parent": value["parent"] if len(chain) == 3 else None,
            "names": value["names"],
            "hidden": value["hidden"] or any(nodes[c]["hidden"] for c in chain[1:]),
        }
    return out


def node(node_id: str) -> dict[str, Any] | None:
    return _index().get(node_id)


def is_stage(node_id: str) -> bool:
    found = node(node_id)
    return bool(found) and found["kind"] == "stage"


def is_subject(node_id: str) -> bool:
    found = node(node_id)
    return bool(found) and found["kind"] == "subject"


def is_difficulty(value: str) -> bool:
    return value in {key for key, _, _ in DIFFICULTIES}


def stage_of(subject_id: str) -> str | None:
    found = node(subject_id)
    return found["stage"] if found else None


def ancestors(subject_id: str) -> list[str]:
    """The subject and its parent, if any: filtering by a parent finds its children."""
    found = node(subject_id)
    if not found:
        return []
    return [subject_id] + ([found["parent"]] if found["parent"] else [])


class TaxonomyError(ValueError):
    pass


def validate_grades(grade_ids: list[str], stages: list[str]) -> list[str]:
    """Grades are optional; each must belong to one of the chosen stages."""
    cleaned = list(dict.fromkeys(str(g) for g in grade_ids or []))
    if len(cleaned) > MAX_GRADES:
        raise TaxonomyError(f"Choose at most {MAX_GRADES} grades.")
    for grade in cleaned:
        stage = grade_stage(grade)
        if stage is None:
            raise TaxonomyError("Unknown grade.")
        if stage not in stages:
            raise TaxonomyError("Each grade must belong to one of the chosen stages.")
    return cleaned


def validate(
    stages: list[str], subjects: list[str], difficulty: str, *, keep: tuple[str, ...] | list[str] = (),
) -> tuple[list[str], list[str], str]:
    """Clean and check a listing's classification; empty lists are allowed here (drafts).

    A hidden entry is refused unless the listing already had it (*keep*, batch 2 §8.1).
    """
    stages = list(dict.fromkeys(str(s) for s in stages or []))
    subjects = list(dict.fromkeys(str(s) for s in subjects or []))
    if len(stages) > MAX_STAGES:
        raise TaxonomyError(f"Choose at most {MAX_STAGES} stages.")
    if len(subjects) > MAX_SUBJECTS:
        raise TaxonomyError(f"Choose at most {MAX_SUBJECTS} subjects.")
    index = _index()
    for value in (*stages, *subjects):
        found = index.get(value)
        if found and found["hidden"] and value not in keep:
            raise TaxonomyError("This entry of the classification is no longer offered.")
    for stage in stages:
        if (index.get(stage) or {}).get("kind") != "stage":
            raise TaxonomyError("Unknown stage.")
    for subject in subjects:
        found = index.get(subject)
        if not found or found["kind"] != "subject":
            raise TaxonomyError("Unknown subject.")
        if found["stage"] not in stages:
            raise TaxonomyError("Each subject must belong to one of the chosen stages.")
    difficulty = str(difficulty or "")
    if difficulty and not is_difficulty(difficulty):
        raise TaxonomyError("Unknown difficulty.")
    return stages, subjects, difficulty


# ── administrators (batch 2 §8.1) ──────────────────────────

NAME_MAX = 40
OTHER_LANGUAGES = ("de", "fr", "pl", "uk")


def _clean_others(names: dict[str, str] | None) -> str | None:
    """The other languages' names as stored; ``None`` when not given."""
    import json

    if names is None:
        return None
    cleaned = {}
    for language in OTHER_LANGUAGES:
        value = _clean_name(names.get(language))
        if value:
            cleaned[language] = value
    return json.dumps(cleaned, ensure_ascii=False)


def _in_use() -> dict[str, int]:
    """How many listings name each node."""
    import json

    from deeptutor.catalog.store import db

    counts: dict[str, int] = {}
    with db.read() as conn:
        rows = conn.execute("SELECT stages_json, subjects_json FROM listings").fetchall() if conn else []
    for row in rows:
        for value in {*json.loads(row["stages_json"] or "[]"), *json.loads(row["subjects_json"] or "[]")}:
            counts[value] = counts.get(value, 0) + 1
    return counts


def admin_tree() -> dict[str, Any]:
    counts = _in_use()
    shaped = tree(include_hidden=True)

    def count(entry: dict[str, Any]) -> dict[str, Any]:
        entry["in_use"] = counts.get(entry["id"], 0)
        return entry

    for stage in shaped["stages"]:
        count(stage)
        for subject in stage["subjects"]:
            count(subject)
            for child in subject["children"]:
                count(child)
    return shaped


def _clean_name(value: str | None) -> str:
    name = " ".join(str(value or "").split())
    if len(name) > NAME_MAX:
        raise TaxonomyError(f"A name has at most {NAME_MAX} characters.")
    return name


def _upsert(conn, node_id: str, **values: Any) -> None:
    conn.execute("INSERT INTO taxonomy_nodes (node_id) VALUES (?) ON CONFLICT(node_id) DO NOTHING", (node_id,))
    for column, value in values.items():
        conn.execute(f"UPDATE taxonomy_nodes SET {column} = ? WHERE node_id = ?", (value, node_id))  # nosec B608


def update_node(node_id: str, *, name_zh: str | None = None, name_en: str | None = None,
                hidden: bool | None = None, names: dict[str, str] | None = None) -> dict[str, Any]:
    from deeptutor.catalog.store import db

    nodes = _nodes()
    if node_id not in nodes:
        raise TaxonomyError("Unknown entry.")
    values: dict[str, Any] = {}
    if name_zh is not None:
        values["name_zh"] = _clean_name(name_zh)
    if name_en is not None:
        values["name_en"] = _clean_name(name_en)
    if nodes[node_id]["custom"] and ("name_zh" in values or "name_en" in values):
        if not (values.get("name_zh", nodes[node_id]["names"]["zh"]) or values.get("name_en", nodes[node_id]["names"]["en"])):
            raise TaxonomyError("Give it a name.")
    if hidden is not None:
        values["hidden"] = int(bool(hidden))
    others = _clean_others(names)
    if others is not None:
        values["names_json"] = others
    with db.write() as conn:
        _upsert(conn, node_id, **values)
    return admin_tree()


def move_node(node_id: str, direction: str) -> dict[str, Any]:
    from deeptutor.catalog.store import db

    nodes = _nodes()
    if node_id not in nodes or direction not in ("up", "down"):
        raise TaxonomyError("Unknown entry.")
    siblings = _children(nodes, nodes[node_id]["parent"], True)
    index = siblings.index(node_id)
    other = index - 1 if direction == "up" else index + 1
    if not 0 <= other < len(siblings):
        return admin_tree()
    siblings[index], siblings[other] = siblings[other], siblings[index]
    with db.write() as conn:
        for position, sibling in enumerate(siblings):
            _upsert(conn, sibling, position=position * 10.0)
    return admin_tree()


def add_node(parent: str, name_zh: str, name_en: str, names: dict[str, str] | None = None) -> dict[str, Any]:
    import uuid

    from deeptutor.catalog.store import db

    nodes = _nodes()
    parent = str(parent or "")
    if parent:
        found = node(parent)
        if found is None or (found["kind"] == "subject" and found["parent"] is not None):
            raise TaxonomyError("Entries go under a stage or a first-level subject.")
    zh, en = _clean_name(name_zh), _clean_name(name_en)
    if not (zh or en):
        raise TaxonomyError("Give it a name.")
    node_id = f"{parent + '.' if parent else ''}custom_{uuid.uuid4().hex[:8]}"
    last = max((nodes[s]["position"] for s in _children(nodes, parent, True)), default=0.0)
    with db.write() as conn:
        conn.execute(
            "INSERT INTO taxonomy_nodes (node_id, parent, name_zh, name_en, hidden, position, custom, names_json) "
            "VALUES (?, ?, ?, ?, 0, ?, 1, ?)",
            (node_id, parent, zh, en, last + 10.0, _clean_others(names) or "{}"),
        )
    return admin_tree()


def delete_node(node_id: str) -> dict[str, Any]:
    from deeptutor.catalog.store import db

    nodes = _nodes()
    if node_id not in nodes:
        raise TaxonomyError("Unknown entry.")
    if not nodes[node_id]["custom"]:
        raise TaxonomyError("Built-in entries cannot be deleted; hide them instead.")
    under = [k for k in nodes if k == node_id or k.startswith(node_id + ".")]
    counts = _in_use()
    if any(counts.get(k) for k in under):
        raise TaxonomyError("Content uses this entry; hide it instead.")
    with db.write() as conn:
        conn.executemany("DELETE FROM taxonomy_nodes WHERE node_id = ?", [(k,) for k in under])
    return admin_tree()


__all__ = [
    "DIFFICULTIES", "GRADE_STAGES", "MAX_GRADES", "MAX_STAGES", "MAX_SUBJECTS", "TaxonomyError", "add_node",
    "admin_tree", "ancestors", "grade_stage", "grades", "validate_grades",
    "delete_node", "is_difficulty", "is_stage", "is_subject", "move_node", "node", "stage_of", "tree",
    "update_node", "validate",
]
