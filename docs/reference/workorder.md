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

---

## Constants

`WORK_ORDER_MARKER` opens every stub failure message, in both languages, so a reader
can tell an unwritten stub from a genuine regression at a glance.
`DEFINITION_OF_DONE` is repeated on every stub: call it and assert on the result;
asserting that it does not raise is insufficient.

The rendering functions behind `build_work_order_files` are internal. Their output is
described in the [Test work orders guide](../guides/work-orders.md).
