# ctypes Writer

The ctypes writer generates Python source code that uses the `ctypes` standard
library to define C type bindings. The output is a runnable Python module
containing struct/union classes, enum constants, type aliases, callback types,
and function prototype annotations.

## Writer Class

::: headerkit.writers.ctypes.CtypesWriter
    options:
      show_source: false

## Convenience Function

::: headerkit.writers.ctypes.header_to_ctypes
    options:
      show_source: false

## Low-Level Functions

These functions are used internally by [`header_to_ctypes`][headerkit.writers.ctypes.header_to_ctypes]
and can be useful when working with individual type expressions.

::: headerkit.writers.ctypes.type_to_ctypes
    options:
      show_source: false

## Example

```python
from headerkit.backends import get_backend
from headerkit.writers import get_writer

backend = get_backend()
header = backend.parse("""
typedef struct {
    int x;
    int y;
} Point;

int distance(Point* a, Point* b);
""", "geometry.h")

writer = get_writer("ctypes", lib_name="_geometry")
print(writer.write(header))
```

Output:

```python
"""ctypes bindings generated from geometry.h."""

import ctypes
import ctypes.util
import sys

# ============================================================
# Structures and Unions
# ============================================================

class Point(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
    ]

# ============================================================
# Function Prototypes
# ============================================================

_geometry.distance.argtypes = [ctypes.POINTER(Point), ctypes.POINTER(Point)]
_geometry.distance.restype = ctypes.c_int
```

## Packed records the writer could not reproduce

A packed record's C layout is not always expressible in ctypes. The writer
records what C says each packed record looks like and emits a check that runs
when the module is imported, comparing that against where ctypes actually put
each field.

The check runs on import rather than at generation because ctypes has changed
its bit-field layout across versions -- `_pack_` selects the MSVC rules from
CPython 3.14, where earlier versions used the System V ones -- so a verdict
computed when the module was written is about the wrong interpreter as soon as
somebody else imports it.

Two things mark an affected record. Its class carries a `# HEADERKIT:` comment
if the generating interpreter already saw the problem, and the module defines:

```python
#: Packed records whose layout this interpreter does not reproduce. Empty
#: when every one of them checks out; absent when the header had none.
HEADERKIT_UNVERIFIED_RECORDS = _hk_unverified_records()
```

The name is defined whenever the header held at least one packed record. An
empty tuple means every one of them was verified **on the importing
interpreter**; the name is absent only when there were no packed records at
all. Read it with a default:

```python
import mybindings

unverified = getattr(mybindings, "HEADERKIT_UNVERIFIED_RECORDS", ())
if unverified:
    raise SystemExit(f"layout not reproduced for: {', '.join(unverified)}")
```

`from mybindings import HEADERKIT_UNVERIFIED_RECORDS` raises `ImportError` on a
module with no packed records. Absent means none.

The check also binds two private helpers, `_HK_PACKED_EXPECTED` and
`_hk_unverified_records`. Both are renamed out of the way if the header
declares something of the same name, so a declaration always wins. The public
name cannot move -- consumers read it to decide whether the bindings are
trustworthy -- so a header declaring `HEADERKIT_UNVERIFIED_RECORDS` is refused
with an error rather than generated with the check under a different name.
