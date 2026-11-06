# Learning Policy Compatibility

Learning restrictions ("learning policy") were removed on 2026-11-06.

- Every non-admin account is an ordinary learner with every feature available:
  the market, courses, practice sets, chat, reading and their personal settings.
- A `learning_policy` stored in an account's grant file is no longer read. The
  file is not rewritten on upgrade; the value is dropped the next time an
  administrator saves that account's assignments.
- The `learner` account preset no longer adds anything to an account.
- Everything else a grant carries (models, skills, tool assignment, the daily
  AI limit) works as before.
