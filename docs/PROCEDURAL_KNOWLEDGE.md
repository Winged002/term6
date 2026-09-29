# Procedural Knowledge Engine

The Procedural Knowledge Engine is term_5's local procedural memory for recurring product/framework/capability/quality patterns.

Skills are declarative JSON only. Loading a skill never executes its contents.

## Manifest

Example:

```json
{
  "name": "social-network",
  "category": "product",
  "description": "Usable social/community MVP",
  "triggers": ["social media", "social network"],
  "requires": ["authentication", "file-uploads"],
  "recommends": ["realtime", "notifications", "web-ux"],
  "features": {
    "core": ["profiles", "posts", "feed"],
    "expected": ["notifications", "search"],
    "optional": ["video"]
  },
  "entities": ["User", "Post"],
  "journeys": ["register -> profile -> post"],
  "pages": ["/feed", "/profile/<username>"],
  "guidelines": ["Do not ship a feed-only demo."],
  "definition_of_done": ["User can complete the critical journey."]
}
```

## Categories

- `product`: application archetypes and expected product scope.
- `framework`: implementation conventions such as Flask.
- `capability`: reusable product capabilities such as uploads/auth/realtime.
- `quality`: UX/security/testing/Docker quality gates.

## Discovery

term_5 resolves skills by trigger/alias/name matches and recursively expands `requires` and `recommends`.

For product-building prompts, generic web quality skills are also added so vague requests receive baseline UX/security/testing/Docker guidance.

## Override locations

```text
~/.term5/skills/**/manifest.json
<workspace>/.term5/skills/**/manifest.json
```

Workspace skills win over user skills, which win over bundled skills when names collide.

## Recommended custom skills

Custom skills are useful for organization-specific conventions. Examples:

```text
syntal-flask-app
syntal-sso
syntal-ui
planner-module
blackbook-module
authentiverse-service
```

A project-specific skill can encode directory conventions, permission names, health endpoints, UI patterns and definition-of-done rules so the model does not rediscover them every task.

## Product planning

When a creation request resolves a `product` skill, term_5 prepares a product blueprint before general execution. Product planning uses HIGH reasoning by default and then runs three bounded critique workers.

The primary model receives the blueprint and critic evidence as locally generated turn context.

## Product audit

`product_audit` reads a bounded source snapshot from indexed text/code files and compares the implementation to the current blueprint with HIGH reasoning.

It cannot visually inspect rendered pages because vision is disabled. A `ready` verdict therefore means source evidence supports the blueprint, not that pixel-level UX has been visually approved.
