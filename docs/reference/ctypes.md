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
builds the class it is about to emit and reads back where ctypes actually put
each field; where that disagrees with the packed C layout, or where a member's
size cannot be read at all, the record is reported rather than emitted as
though correct.

Two things mark such a record. The class carries a `# HEADERKIT:` comment
naming a field whose placement is wrong, and the module defines a tuple:

```python
#: Records in this module whose layout ctypes could not reproduce.
#: Each also carries a '# HEADERKIT:' comment on its class.
HEADERKIT_UNVERIFIED_RECORDS = (
    "SomeRecord",
)
```

The tuple is defined **only when it would be non-empty**, so that a module with
nothing to report is byte-for-byte what it would have been otherwise. Read it
with a default rather than importing the name directly:

```python
import mybindings

unverified = getattr(mybindings, "HEADERKIT_UNVERIFIED_RECORDS", ())
if unverified:
    raise SystemExit(f"layout not reproduced for: {', '.join(unverified)}")
```

`from mybindings import HEADERKIT_UNVERIFIED_RECORDS` raises `ImportError` on a
clean module. Absent means none.

The check is made against the interpreter that *generates* the module. ctypes
has changed its bit-field layout across versions -- `_pack_` selects the MSVC
rules from CPython 3.14, where earlier versions used the System V ones -- so a
module generated on one interpreter and imported on another can be laid out
differently from the one that was measured. Generate on the interpreter you
will run.
