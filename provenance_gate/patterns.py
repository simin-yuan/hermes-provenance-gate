"""Claim patterns and exemptions, shipped as data. ``claims.py`` compiles and applies them.

A sentence is a *decision claim* when a pattern below matches with the user as the grammatical
subject. Every pattern carries examples that must match; ``OPEN_FORM_EXAMPLES`` lists negated,
open and conditional forms that must not, and ``GENERIC_ACTOR_EXAMPLES`` lists generic-actor text
(specs, QA notes, UI steps, policies) that must not. The tests run every entry. This list is a
tripwire, not a complete filter: wording outside it is not checked.
"""

from __future__ import annotations

# --- English ---------------------------------------------------------------------------------

# Only these subjects count; pronouns alone ("he", "they") and the plural "users" (a population,
# not the user) never trigger.
EN_SUBJECT = r"(?:the\s+(?:user|owner)|user|owner)"

# Optional auxiliary and adverbs between the subject and the verb. Negations ("not", "n't",
# "never") and modals ("may", "will", "might") are deliberately absent, so "has not decided" or
# "will decide later" cannot match.
EN_AUX = r"(?:\s+(?:has|had))?"
EN_ADVERBS = (r"(?:\s+(?:already|explicitly|clearly|finally|now|just|also|officially|personally|formally|"
              r"then|today|yesterday|ultimately|eventually|firmly|verbally|really|actually|definitely|"
              r"indeed|personally))*")
# An optional short parenthetical right after the subject: "The user (Dana) approved ...".
EN_SUBJECT_PAREN = r"(?:\s*\([^()\n]{1,40}\))?"

# A bare "user"/"owner" (no "the") counts only at the start of a clause: at the start of the
# sentence, after punctuation, or after one of these words. "Repo owner approved" does not count.
EN_BARE_SUBJECT_AFTER = frozenset({
    "and", "but", "so", "then", "because", "since", "that", "after", "today", "yesterday",
    "finally", "also", "later", "earlier", "now", "here", "note", "nb", "update", "decision",
    "my", "our",
})

# A subject within three words after one of these is a conditional or reported-question form.
# Openers that are not conditional ("When asked,", "Before lunch,", "Today", "In the meeting")
# do not exempt a claim.
EN_CONDITIONALS = frozenset({"if", "unless", "whether", "once", "until", "till"})

# A subject preceded (earlier in the same sentence) by one of these words is a generic actor in
# a study, scenario, example or specification, not the person the agent works for.
EN_GENERIC_CONTEXT = frozenset({
    "study", "usability", "scenario", "scenarios", "persona", "prototype", "mockup", "wireframe",
    "simulation", "simulated", "experiment", "walkthrough", "repro", "reproduce", "example",
    "hypothetically", "suppose", "imagine", "typically", "usually", "generally",
    "spec", "specification", "e.g", "eg", "flow", "journey", "funnel", "checkout", "onboarding",
})
# The generic context only reaches across commas, not across a contrast or a new clause:
# "Usually we wait, but the user approved ..." is a claim.
EN_CLAUSE_BREAKS = (";", " but ", " however", " yet ", " though ", " although ", " whereas ", " still ")
# A noun right after the verb makes the phrase a field or column name: "the user approved column".
EN_FIELD_NOUNS = frozenset({
    "column", "columns", "field", "fields", "flag", "flags", "attribute", "property", "checkbox", "status",
    "state", "timestamp", "date", "bit", "boolean", "key", "value", "values", "count", "rate", "event",
    "events", "list", "table", "screen", "modal", "dialog", "button", "page", "step", "state.",
})

# A sentence that starts with "<label>:" (optionally numbered, e.g. "Bug #12:", "Step 3:") is
# a QA or specification line about a generic user.
EN_SPEC_LABELS = (
    "bug", "issue", "ticket", "case", "test", "test case", "step", "steps", "scenario", "given",
    "when", "then", "expected", "actual", "repro", "steps to reproduce", "story", "user story",
    "acceptance criteria", "ac", "example", "precondition", "postcondition", "flow", "use case",
    "support ticket", "support ticket summary", "customer interview", "customer interview notes",
    "interview notes", "user interview", "process note", "policy", "changelog", "release notes", "faq",
    "error", "docs", "documentation", "survey", "feedback", "review",
)
# Earlier in the same clause, one of these reports what a source says: "Upstream issue #4411 says
# the owner approved ...", "Docs say the user decided ...", "Error E401 means the user rejected ...".
EN_REPORTED_VERBS = frozenset({"says", "say", "said", "states", "reported", "means", "mentions", "explains",
                               "describes"})
# A sentence starting with this word (Gherkin) is a specification step.
EN_SPEC_STARTS = ("given",)

# name -> (verb regex, examples that must be claims)
EN_DECISION_VERBS: dict[str, tuple[str, tuple[str, ...]]] = {
    "decided": (r"decided", ("User decided to ship on Friday.", "The user has decided to drop IE support.")),
    "chose": (r"(?:chose|chosen)", ("The user chose Postgres over MySQL.", "Owner has chosen the blue theme.")),
    "agreed": (r"agreed", ("The user agreed to move the launch.",)),
    "approved": (r"approved", ("The owner approved the budget.", "User already approved the migration.")),
    "confirmed": (r"confirmed", ("The user confirmed the release date is 3 May.",)),
    "signed off": (r"signed\s+off(?!\s+(?:with|as|by|from)\b)", ("The user signed off on the design.",)),
    "instructed": (r"instructed", ("The user instructed me to keep the old API.",)),
    "asked me to": (r"asked\s+(?:me|us)\s+to", ("The user asked me to delete the staging bucket.",)),
    "told me to": (r"told\s+(?:me|us)\s+to", ("User told me to stop using tabs.",)),
    "wants us to": (r"wants\s+(?:me|us)\s+to", ("The user wants us to rewrite it in Rust.",)),
    "said to": (r"said\s+to", ("The user said to skip the tests.",)),
    "gave the go-ahead": (r"gave\s+(?:the\s+|a\s+)?go[\s-]ahead", ("The owner gave the go-ahead for the rollout.",)),
    "rejected": (r"rejected", ("The user rejected the proposal.",)),
    "vetoed": (r"vetoed", ("The owner vetoed the rename.",)),
    "OK'd": (r"(?:ok['’]d|okayed|okay['’]d)", ("The user OK'd the migration.", "The owner okayed the spend.")),
    "said yes to": (r"said\s+yes\s+to", ("The user said yes to the redesign.",)),
    "greenlit": (r"(?:greenlit|green[\s-]lit)", ("The owner greenlit the launch.", "The user green-lit the rewrite.")),
}

# Phrases that attribute a decision to the user without a subject-verb pair.
# name -> (regex, examples that must be claims)
EN_DECISION_PHRASES: dict[str, tuple[str, tuple[str, ...]]] = {
    "the user's decision": (
        r"(?<![\w-])(?:the\s+)?(?:user|owner)['’]s\s+(?:final\s+|explicit\s+|own\s+)?"
        r"(?:decision|call|choice|instruction|instructions|ruling|verdict)\s*(?::|—|\bwas\b)",
        ("The user's decision: ship on Friday.", "The owner's final call was to drop Windows 7.")),
    "per the user": (
        r"(?<![\w-])per\s+the\s+(?:user|owner)(?![\w-])(?!\s+(?:basis|account|session|limit))",
        ("Ship on Friday, per the user.", "Per the user's request, we dropped IE.")),
    "according to the user": (
        r"(?<![\w-])according\s+to\s+(?:the\s+)?(?:user|owner)(?:['’]s\s+(?:instructions?|request|decision|wishes|"
        r"direction|call)\b|\s*(?:[,.;:]|$))",
        ("According to the user's instructions, logs stay for 30 days.", "Ship it on Friday, according to the user.")),
}

EN_PREFERENCE_VERBS: dict[str, tuple[str, tuple[str, ...]]] = {
    "prefers": (r"prefers", ("The user prefers dark mode.",)),
    "likes": (r"likes", ("User likes short answers.",)),
    "loves": (r"loves", ("The user loves type hints.",)),
    "dislikes": (r"dislikes", ("The user dislikes emoji in commits.",)),
    "hates": (r"hates", ("The owner hates long meetings.",)),
    "would rather": (r"would\s+rather", ("The user would rather use pnpm.",)),
}

# A trailing parenthetical or tag question does not make a statement a question.
EN_TAG_QUESTIONS = ("ok", "okay", "right", "yes", "no", "correct", "agreed", "eh", "yeah", "yep",
                    "isn't it", "didn't they", "didn't he", "didn't she", "didn't you", "did they",
                    "no?", "true")

# --- Chinese (simplified and traditional) ---------------------------------------------------

ZH_SUBJECT = r"(?:用户|用戶|主人|老板|老闆)"
# Negations (未, 没, 不, 尚未, 还没) and 是否 / 待 are deliberately absent. Whitespace between the
# subject, adverbs and verb is allowed.
ZH_ADVERBS = (r"(?:\s*(?:已经|已經|明确地|明確地|明确|明確|正式地|正式|最终|最終|最后|最後|亲自|親自|当场|當場|"
              r"刚刚|剛剛|刚才|剛才|今天|昨天|已|刚|剛|也|都))*")

# The subject right after one of these is an open form: 待用户确认, 由用户决定, 如果用户同意 ...
ZH_OPEN_BEFORE = ("待", "由", "等", "等待", "请", "請", "需", "需要", "须", "須", "让", "讓", "若", "如果",
                  "假如", "假设", "假設", "一旦", "是否", "交给", "交給", "应由", "應由", "要", "看")

# 主人 / 老板 right after one of these is a third party (仓库主人, 店老板), not the user; 用户 right
# after one of these is a population (新用户, 测试用户), not the user.
ZH_THIRD_PARTY_BEFORE = ("仓库", "倉庫", "项目", "項目", "店", "公司", "群", "网站", "網站", "频道", "頻道",
                         "房", "车", "車", "狗", "猫", "貓", "宠物", "寵物", "商店", "店铺", "店鋪", "新", "老",
                         "普通", "目标", "目標", "测试", "測試", "终端", "終端", "匿名", "所有", "每个", "每個",
                         "某些", "部分", "付费", "付費", "免费", "免費", "注册", "註冊", "一些", "该", "該")

# A verb followed by one of these is conditional, a question, or part of a noun (确认后, 同意吗,
# 确认框 ...) — unless the next character turns it into another word (后天, 前端, 时间), listed
# as the exclusions.
ZH_OPEN_AFTER: dict[str, str] = {
    "后": "天端台面续缀者来期备", "後": "天端台面續綴者來期備", "之后": "", "之後": "", "以后": "", "以後": "",
    "前": "端台面天提景往夕辈輩", "之前": "", "以前": "",
    "时": "间间刻区速长效代机钟", "時": "間刻區速長效代機鐘", "的话": "", "的話": "", "与否": "", "與否": "",
    "吗": "", "嗎": "", "么": "", "麼": "", "权": "", "權": "",
    "框": "", "弹窗": "", "彈窗": "", "页面": "", "頁面": "", "页": "", "頁": "", "按钮": "", "按鈕": "",
    "对话框": "", "對話框": "", "流程": "", "邮件": "", "郵件": "", "机制": "", "機制": "", "环节": "",
    "環節": "", "步骤": "", "步驟": "", "界面": "",
}

# A rule or condition in the same clause after the verb: 用户同意隐私政策才能注册.
ZH_CONDITION_MARKERS = ("才能", "才可以", "才可", "方可", "即可", "才会", "才會", "则", "則", "就可以", "就能",
                        "后会", "後會", "后就", "後就", "后才", "後才", "后再", "後再", "后系统", "後系統")
# Anywhere in the sentence: a script, record or process description, not the user's own decision.
ZH_SOURCE_MARKERS = ("话术", "話術", "这是销售", "這是銷售", "的记录", "的記錄", "流程第", "这是下单", "這是下單",
                     "文档里", "文檔裡", "文档中", "规则：", "規則：")

# A clause (up to the next comma or full stop) ending in one of these is a condition:
# 用户确认收货后，订单完成.
ZH_CLAUSE_FINAL_CONDITIONS = ("后", "後", "之后", "之後", "以后", "以後", "时", "時", "的时候", "的時候")

# A Chinese verb directly followed by one of these forms a noun (确认码, 批准率, 指示灯, 同意书 ...).
ZH_NOUN_SUFFIXES = ("码", "碼", "率", "灯", "燈", "书", "書", "单", "單", "记录", "記錄", "状态", "狀態", "字段",
                    "比例", "次数", "次數", "人数", "人數", "数量", "數量", "函", "表", "栏", "欄", "项", "項", "位")
# In the clause after the verb, a 的 followed by one of these makes the whole phrase the subject of
# another statement: 用户确认支付的回调可能重复; 用户拒绝了推送权限的比例是 41%.
ZH_PREDICATES_AFTER_DE = ("可能", "会", "會", "需要", "是", "为", "為", "将", "將", "应该", "應該", "应", "應")
# A key/value line ("user confirmed: false") is data when its key has at most this many words.
KEY_MAX_WORDS = 4

# Earlier in the sentence: a test, example or scenario, not the user's own decision.
ZH_GENERIC_CONTEXT = ("测试中", "測試中", "测试时", "測試時", "用例", "场景", "場景", "示例", "例如", "比如",
                      "演示", "原型", "可用性测试", "可用性測試", "实验中", "實驗中", "调研中", "調研中",
                      "模拟", "模擬", "假设", "假設")

ZH_DECISION_VERBS: dict[str, tuple[str, tuple[str, ...]]] = {
    "决定": (r"(?:决定|決定)", ("用户决定周五发布。", "用户已决定不再支持 IE。", "用戶決定週五發布。")),
    "同意": (r"同意", ("用户同意推迟上线。",)),
    "批准": (r"(?:批准|批準)", ("老板批准了预算。", "老闆批準了預算。")),
    "确认": (r"(?:确认|確認)", ("用户已经确认发布日期是五月三日。", "用戶確認了發布日期。")),
    "拍板": (r"拍板", ("老板拍板用 Postgres。",)),
    "要求": (r"要求", ("用户要求保留旧接口。",)),
    "指示": (r"指示", ("主人指示我删除测试桶。",)),
    "让我": (r"(?:让我|讓我)", ("用户让我停止使用制表符。",)),
    "选择了": (r"(?:选择了|選擇了)", ("用户选择了蓝色主题。",)),
    "否决": (r"(?:否决|否決)", ("老板否决了改名方案。",)),
    "拒绝了": (r"(?:拒绝了|拒絕了)", ("用户拒绝了这个提议。", "用戶拒絕了這個提議。")),
}

ZH_PREFERENCE_VERBS: dict[str, tuple[str, tuple[str, ...]]] = {
    "喜欢": (r"(?:更)?(?:喜欢|喜歡)", ("用户喜欢简短的回答。", "用户更喜欢深色模式。")),
    "偏好": (r"偏好", ("用户偏好使用 pnpm。",)),
    "倾向于": (r"(?:倾向于|傾向於)", ("用户倾向于先写测试。",)),
    "讨厌": (r"(?:讨厌|討厭)", ("老板讨厌冗长的会议。",)),
}

# --- Extra claim forms that must match --------------------------------------------------------

CLAIM_VARIANT_EXAMPLES: tuple[str, ...] = (
    "Update: user decided to drop Windows 7.", "After review, the user approved the plan.",
    "Note that the owner vetoed the rename.", "**User decided** to freeze the API.",
    "The user has now explicitly approved the rollout.", "THE USER DECIDED TO SHIP.",
    "Yesterday the owner signed off on the copy.", "My user decided to rename the repo.",
    "When asked, the user approved the migration.", "Before lunch, the user decided to ship.",
    "Today the user chose Postgres.", "In the meeting the owner vetoed the rename.",
    "The user approved the migration (ticket?)", "The user decided to ship on Friday, ok?",
    "The user ap\u00adproved the rollout.", "The user de\u200bcided to ship.", "Ｔｈｅ ｕｓｅｒ ａｐｐｒｏｖｅｄ ｉｔ.",
    "用户已经确认发布日期。", "老板最终拍板了。", "用户 决定 周五发布。", "用户明确地同意了。", "用户正式批准了方案。",
    "用户确认后天发布。", "用户决定前端用 React。", "用户确认时间定在周五。",
)

# --- Forms that must NOT be claims -----------------------------------------------------------

# Negated and open forms (every one is tested). Questions are also open: a sentence that ends
# in "?" or "？" (after dropping a trailing parenthetical or tag question) is never a claim.
OPEN_FORM_EXAMPLES: dict[str, tuple[str, ...]] = {
    "has not decided": ("The user has not decided yet.", "The user has not yet decided on a name."),
    "hasn't approved": ("The user hasn't approved the budget.", "The owner hasn’t approved it."),
    "didn't agree": ("The user didn't agree to the plan.", "The user did not agree."),
    "never": ("The user never confirmed the date.",),
    "may decide": ("The user may decide next week.", "The owner might approve it."),
    "will decide later": ("The user will decide later.",),
    "asked whether": ("The user asked whether we can ship on Friday.",),
    "to be confirmed": ("Release date: to be confirmed by the user.", "Whether the user approved is unclear."),
    "if/once/until": ("If the user approved, proceed.", "Once the user has decided, update the plan.",
                      "Hold the release until the owner has confirmed it.", "Unless the user decided otherwise, keep it."),
    "question": ("Has the user decided?", "Did the user approve the migration?", "用户确认了吗？"),
    "plural users": ("Users decided to stay on the old plan.", "The users approved it in the survey."),
    "未决定": ("用户未决定发布日期。",),
    "还没确认": ("用户还没确认。",),
    "尚未同意": ("用户尚未同意该方案。",),
    "待确认": ("发布日期待用户确认。", "方案待老板批准。"),
    "是否同意": ("用户是否同意还不清楚。", "需确认用户是否同意。"),
    "由用户决定": ("发布时间由用户决定。", "这一点交给用户决定。"),
    "如果用户同意": ("如果用户同意，就周五发布。",),
    "确认后": ("用户确认后再部署。", "用户确认收货后，订单完成。"),
    "不同意": ("用户不同意。",),
    "noun use": ("弹出用户确认框。", "用户确认页面已上线。"),
}

# Generic-actor text: product, UX and QA docs, UI steps, policies and third parties.
GENERIC_ACTOR_EXAMPLES: tuple[str, ...] = (
    "Bug #12: the user confirmed the email but the link expired.",
    "In the usability study, the user chose the cheaper plan.",
    "on_confirm: user confirmed the modal, emit event",
    "user confirmed: false",
    "user approved = true",
    "Step 3: the user confirmed the order.",
    "Given the user confirmed the email, show the dashboard.",
    "Expected: the user approved the invoice and the status is PAID.",
    "For example, the user chose dark mode and the setting persisted.",
    "Each user confirmed the address before checkout.",
    "The repo owner approved the PR.",
    "用户同意隐私政策才能注册。",
    "测试中用户选择了蓝色按钮。",
    "仓库主人确认了这个修改。",
    "新用户确认邮箱后才能登录。",
    "例如用户选择了深色模式，设置会保存。",
)

# --- Relatedness of a quote to its claim ------------------------------------------------------

# Latin content words are 3+ letters, lower-cased, minus these.
STOPWORDS = frozenset("""
the and for with that this from have has had was were are will would can could should shall
you your yours our ours they them their theirs she her hers his him its not but all any some
user users owner owners about into onto also just then than there here what when where which
who whom whose why how please okay yes did does done doing get got gets let lets now very
really want wants need needs said says say tell told ask asked asks one two out off over
too much many more most other such only own same both each few being been may might must
let's i'm it's that's we're they're you're don't doesn't didn't isn't wasn't aren't weren't
""".split())
# CJK content is character bigrams, minus these.
ZH_STOP_BIGRAMS = frozenset({"用户", "用戶", "主人", "老板", "老闆", "我们", "我們", "你们", "你們", "他们",
                             "他們", "一下", "这个", "這個", "那个", "那個", "就是", "的话", "的話", "原话",
                             "原話", "用户原", "户原"})
# A quote and the claim sentence (with the quote removed) must share this many content tokens.
MIN_SHARED_TOKENS = 2

# --- Negation right before a quoted span in the user's message (quotes.negated_at) -------------

EN_NEGATIONS = ("never", "not", "no", "without", "dont", "cannot", "nor", "neither", "stop", "avoid")
ZH_NEGATION_CHARS = frozenset("不别別勿没沒未禁绝絕莫甭")
