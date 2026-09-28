# Learning Policy Compatibility

The permission model follows upstream `main`: learner policies support chat
and immersive reading. Teacher/student roles and the learner account preset
remain separate concepts.

Develop-only surface and capability extensions are retired. Version-2 local
learning policies are projected on read:

- Keep only upstream-supported capabilities and surfaces.
- Preserve material assignments, upload restrictions, age band, and resource grants.
- Replace retired peer/research-assistant locked personas with teacher.
- Select a retained capability when the previous default was removed.
- Leave the stored file unchanged until an administrator saves the grant.
  Saving uses upstream's atomic write and cross-process locking.

No account is broadened to the default learner grant. A customized policy with
no remaining supported capability or surface is rejected and requires an
administrator to submit a valid replacement grant. The original file is retained.
Unversioned policies continue through upstream validation.

Learner personas remain protected: the locked persona is loaded only from
administrator-authored presets, and a missing preset refuses the turn.
Accounts without a learning policy use upstream workspace-first persona lookup,
including administrator fallback from explicit workspaces.

This code change does not rewrite production data or deploy the application.
