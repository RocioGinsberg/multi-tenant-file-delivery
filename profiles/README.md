# Classification Profiles

`profiles/` contains versioned business adapters for the classifier engine.

The platform classifier core does not encode concrete business vocabulary such
as team names, report names, or folder hierarchies. A profile defines how a
specific business context maps uploaded files into:

- `target`: logical recipient; Phase 1 stores a matched name, later maps to a workspace
- `document_type`: business document type
- `category`: grouping label for preview and path rendering
- `dst_path`: relative path inside the target workspace

Phase 1 loads one local static profile. Draft/publish, UI editing, and database
registry versions are deferred to Phase 6.5+.
