"""Claim patterns, shipped as data. ``claims.py`` compiles and applies them.

A sentence is a *decision claim* when a pattern below matches with the user as the grammatical
subject. Each pattern carries examples that must match; ``OPEN_FORM_EXAMPLES`` lists negated and
open forms that must not. The tests run every entry. This list is a tripwire, not a complete
filter: wording outside it is not checked.
"""

from __future__ import annotations

# --- English ---------------------------------------------------------------------------------

# Only these subjects count; pronouns alone ("he", "she", "they") never trigger.
EN_SUBJECT = r"(?:the\s+(?:user|owner)|user|owner)"

# Optional auxiliary and adverbs between the subject and the verb. Negations ("not", "n't",
# "never") and modals ("may", "will", "might") are deliberately absent, so "has not decided" or
# "will decide later" cannot match.
EN_AUX = r"(?:\s+(?:has|had))?"
EN_ADVERBS = r"(?:\s+(?:already|explicitly|clearly|finally|now|just|also|officially|personally|formally|then|today|yesterday|ultimately))*"

# A bare "user"/"owner" (no "the") counts only at the start of a clause: at the start of the
# sentence, after punctuation, or after one of these words. "Repo owner approved" does not count.
EN_BARE_SUBJECT_AFTER = frozenset({
    "and", "but", "so", "then", "because", "since", "that", "after", "today", "yesterday",
    "finally", "also", "later", "earlier", "now", "here", "note", "nb", "update", "decision", "my", "our",
})

# A subject right after one of these words is an open (conditional or reported-question) form.
EN_OPEN_BEFORE = frozenset({
    "whether", "if", "unless", "until", "once", "when", "whenever", "before", "till",
})

# name -> (verb regex, examples that must be claims)
EN_DECISION_VERBS: dict[str, tuple[str, tuple[str, ...]]] = {
    "decided": (r"decided", ("User decided to ship on Friday.", "The user has decided to drop IE support.")),
    "chose": (r"(?:chose|chosen)", ("The user chose Postgres over MySQL.", "Owner has chosen the blue theme.")),
    "agreed": (r"agreed", ("The user agreed to move the launch.",)),
    "approved": (r"approved", ("The owner approved the budget.", "User already approved the migration.")),
    "confirmed": (r"confirmed", ("The user confirmed the release date is 3 May.",)),
    "signed off": (r"signed\s+off", ("The user signed off on the design.",)),
    "instructed": (r"instructed", ("The user instructed me to keep the old API.",)),
    "asked me to": (r"asked\s+(?:me|us)\s+to", ("The user asked me to delete the staging bucket.",)),
    "told me to": (r"told\s+(?:me|us)\s+to", ("User told me to stop using tabs.",)),
    "wants us to": (r"wants\s+(?:me|us)\s+to", ("The user wants us to rewrite it in Rust.",)),
    "said to": (r"said\s+to", ("The user said to skip the tests.",)),
    "gave the go-ahead": (r"gave\s+(?:the\s+|a\s+)?go[\s-]ahead", ("The owner gave the go-ahead for the rollout.",)),
    "rejected": (r"rejected", ("The user rejected the proposal.",)),
    "vetoed": (r"vetoed", ("The owner vetoed the rename.",)),
}

EN_PREFERENCE_VERBS: dict[str, tuple[str, tuple[str, ...]]] = {
    "prefers": (r"prefers", ("The user prefers dark mode.",)),
    "likes": (r"likes", ("User likes short answers.",)),
    "loves": (r"loves", ("The user loves type hints.",)),
    "dislikes": (r"dislikes", ("The user dislikes emoji in commits.",)),
    "hates": (r"hates", ("The owner hates long meetings.",)),
    "would rather": (r"would\s+rather", ("The user would rather use pnpm.",)),
}

# --- Chinese ---------------------------------------------------------------------------------

ZH_SUBJECT = r"(?:用户|主人|老板)"
# Negations (未, 没, 不, 尚未, 还没) and 是否 / 待 are deliberately absent.
ZH_ADVERBS = r"(?:已经|已|刚刚|刚才|刚|也|都|明确|正式|最终|最后|亲自|当场|今天|昨天)*"

# The subject right after one of these is an open form: 待用户确认, 由用户决定, 如果用户同意 ...
ZH_OPEN_BEFORE = ("待", "由", "等", "等待", "请", "需", "需要", "须", "让", "若", "如果", "假如",
                  "一旦", "是否", "交给", "应由", "要", "看")
# A verb followed by one of these is conditional, a question, or a noun (确认后, 同意吗, 确认框 ...).
ZH_OPEN_AFTER = ("后", "之后", "以后", "前", "之前", "的话", "时", "与否", "吗", "么", "权",
                 "框", "弹窗", "页面", "页", "按钮", "对话框", "流程", "邮件", "机制", "环节", "步骤", "界面")

ZH_DECISION_VERBS: dict[str, tuple[str, tuple[str, ...]]] = {
    "决定": (r"决定", ("用户决定周五发布。", "用户已决定不再支持 IE。")),
    "同意": (r"同意", ("用户同意推迟上线。",)),
    "批准": (r"批准", ("老板批准了预算。",)),
    "确认": (r"确认", ("用户已经确认发布日期是五月三日。",)),
    "拍板": (r"拍板", ("老板拍板用 Postgres。",)),
    "要求": (r"要求", ("用户要求保留旧接口。",)),
    "指示": (r"指示", ("主人指示我删除测试桶。",)),
    "让我": (r"让我", ("用户让我停止使用制表符。",)),
    "选择了": (r"选择了", ("用户选择了蓝色主题。",)),
    "否决": (r"否决", ("老板否决了改名方案。",)),
    "拒绝了": (r"拒绝了", ("用户拒绝了这个提议。",)),
}

ZH_PREFERENCE_VERBS: dict[str, tuple[str, tuple[str, ...]]] = {
    "喜欢": (r"(?:更)?喜欢", ("用户喜欢简短的回答。", "用户更喜欢深色模式。")),
    "偏好": (r"偏好", ("用户偏好使用 pnpm。",)),
    "倾向于": (r"倾向于", ("用户倾向于先写测试。",)),
    "讨厌": (r"讨厌", ("老板讨厌冗长的会议。",)),
}

# --- Forms that must NOT be claims -----------------------------------------------------------

# Negated and open forms (every one is tested). Questions are also open: a sentence that ends
# in "?" or "？" once quoted spans are removed is never a claim.
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
                      "Hold the release until the owner has confirmed it."),
    "question": ("Has the user decided?", "Did the user approve the migration?", "用户确认了吗？"),
    "未决定": ("用户未决定发布日期。",),
    "还没确认": ("用户还没确认。",),
    "尚未同意": ("用户尚未同意该方案。",),
    "待确认": ("发布日期待用户确认。", "方案待老板批准。"),
    "是否同意": ("用户是否同意还不清楚。", "需确认用户是否同意。"),
    "由用户决定": ("发布时间由用户决定。", "这一点交给用户决定。"),
    "如果用户同意": ("如果用户同意，就周五发布。",),
    "确认后": ("用户确认后再部署。",),
    "不同意": ("用户不同意。",),
    "noun use": ("弹出用户确认框。", "用户确认页面已上线。"),
}
