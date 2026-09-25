# Gulyas Tasks — mobile app spec (tutorial)

A minimal 3-screen mobile todo app. Small enough to build test-first in one
sitting, real enough that every TDD step maps to visible UI behaviour.

## 1. Screens

| # | Screen | Shows | Actions |
|---|--------|-------|---------|
| 1 | List | task rows (`title` + checkbox), filter tabs `All / Active / Done`, count badge | toggle, delete, switch filter, clear-completed |
| 2 | Add | text field + Save | `add(title)`; blank title → inline error, no save |
| 3 | (state, not a nav screen) | empty-list illustration when filter yields 0 rows | — |

Navigation is trivial (List ⇄ Add); all logic lives in the core below.

## 2. Portable core (what TDD covers)

`TodoStore` — plain data + rules, no UI framework imports:

```python
store = TodoStore()
tid = store.add("Buy milk")     # -> int id, ValueError on blank title
store.toggle(tid)               # active <-> done, KeyError on unknown id
store.remove(tid)               # KeyError on unknown id
store.list("all")               # insertion order
store.list("active")            # done == False
store.list("done")              # done == True
store.clear_done()              # -> int removed
d = store.to_dict()             # JSON-able snapshot (for persistence)
store2 = TodoStore.from_dict(d) # round-trips exactly
```

Rules: titles are stripped, blank rejected; ids are ints, monotonically
increasing, never reused after `remove`/`clear_done`; `list()` returns copies;
unknown filter string → `ValueError`.

## 3. Out of scope (kept out so the loop stays offline)

Push sync, auth, image attachments, widgets, notifications — none of these
are needed to teach the RED → GREEN → REFACTOR shape.

## 4. Thin-UI mapping (core stays identical)

The same core ports 1:1; only the binding is native:

| Concept | Python (tutorial) | Kotlin / Jetpack Compose | Swift / SwiftUI |
|---------|-------------------|--------------------------|-----------------|
| Item | `{"id","title","done"}` dict | `data class Task(id:Int,title:String,done:Boolean)` | `struct Task: Identifiable` |
| Store | `TodoStore` | `TaskViewModel : ViewModel` wrapping the same methods | `TaskStore : ObservableObject` |
| List All/Active/Done | `store.list(f)` | `tasks.filter { … }.collectAsState()` | `tasks.filter { … }` in `List` |
| Add validation | `ValueError` → test asserts | `if (title.isBlank()) showError()` | `guard !title.isEmpty else …` |
| Persistence | `to_dict/from_dict` | `kotlinx.serialization` snapshot | `Codable` snapshot |

Build the native shell *after* the core is green: bind each widget to one
already-tested method. A UI bug then means a binding bug, never a logic bug.
