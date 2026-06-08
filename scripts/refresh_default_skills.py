"""Refresh every agent's active GLOBAL skill to the current code default.

WHY: `seed_default_skills()` (run at startup) only creates a skill the first time
and skips agents that already exist, so an existing database never adopts improved
`_DEFAULT_SKILLS` text. After the prompts/skills were re-engineered, run this once to
push the new defaults into an already-seeded database:

    python scripts/refresh_default_skills.py

It creates a NEW active version per changed agent and archives the previously-active
one — so any earlier admin-tuned text is preserved in version history (revertable from
the Skill Layer UI), not destroyed. Agents already matching the default are left
untouched. System-prompt improvements do NOT need this script; they take effect on the
next backend restart regardless.
"""
import asyncio
import os
import sys

# Make `app...` importable when run from the project root.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.join(_ROOT, "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)


async def _main() -> None:
    from app.modules.skill_layer.service import refresh_default_skills

    print("Refreshing active global skills to current code defaults…", flush=True)
    count = await refresh_default_skills()
    if count == 0:
        print("All agent skills already match the current defaults — nothing to do.")
    else:
        print(f"Done. {count} agent skill(s) updated (previous versions archived).")


if __name__ == "__main__":
    asyncio.run(_main())
