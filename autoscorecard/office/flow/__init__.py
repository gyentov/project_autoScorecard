"""The Office, split by domain into mixins (composed in office/workflow.py).

    data                 DataMixin                Data access for the team and the tools: dataset, target, cut artifacts, summaries.
    handover             HandoverMixin            The Boss's actions, the data handover with Laras, and messages passed on.
    relays               RelayMixin               Teamwork: orders dispatched to the owner, relays (several teammates in turn), ad hoc meetings.
    conversation         ConversationMixin        What the Boss types: routing, the free rule-based replies (status, timeline, cost), meeting turns.
    stages               StagesMixin              Work the Boss can watch, the stage meetings (kickoff, data, target) and the vintage analysis.
    deliverables_orders  DeliverableOrdersMixin   Orders the office can do itself: documentation, deck, feature meanings.
    dictionary           DictionaryMixin          Bening's dictionary: its versions, Laras's review, meanings from discussions, derived variables.
    eda                  EdaMixin                 Bening's EDA: descriptive statistics, her points, Citra's review, their discussion, Laras's check.
    plan                 PlanMixin                Citra's waterfall plan meeting and the plan editor.
    cuts                 CutsMixin                Model cuts: build (tuning on the final cut), results meeting with the deck on the projector.
    score_report         ScoreReportMixin         Citra's score report: contribution, bins, score bands (20 -> ideal), KS, risk levels.
"""
