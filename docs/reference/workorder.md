# Work Order API Reference

The `headerkit.workorder` module assigns every declaration in a parsed unit to a test
tier and renders the generated test file and its markdown companions for a scaffolded
project.

Tier 1 emits real, passing tests where the IR determines both the call and the expected
result. Tiers 2 and 3 emit deliberately failing stubs for behaviour no header records.
See the [Test work orders guide](../guides/work-orders.md) for what each tier covers and
why the exclusions are exclusions.

---

## Classes

::: headerkit.workorder.WorkOrder

::: headerkit.workorder.Tier1Test

::: headerkit.workorder.Stub

---

## Functions

::: headerkit.workorder.analyze_work_order

::: headerkit.workorder.build_work_order_files

::: headerkit.workorder.render_python_tests

::: headerkit.workorder.render_nim_tests

::: headerkit.workorder.render_nim_dsl

::: headerkit.workorder.render_signature

::: headerkit.workorder.render_work_order_md

::: headerkit.workorder.render_suggestions_md

::: headerkit.workorder.render_agents_md
