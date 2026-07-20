# MinecraftPorter architecture

The update path has one owner: `PortingEngine`.

```
Browser (JavaScript) -> FastAPI request adapter -> PortingEngine -> updated archive
                                                    |-> metadata transformer
                                                    |-> source/data migration rules
                                                    `-> compatibility report
```

When more than one archive is selected, the request adapter builds a dependency
batch: each supplied mod is ported separately, the output is returned as one ZIP,
and `dependency-report.json` identifies declared dependencies that were not
provided in the upload.

## Language boundaries

- **JavaScript** owns the browser workflow: uploads, progress, error handling, and presenting the report returned by the API.
- **Python** owns archive orchestration and declarative source/data migration rules. Python's standard library is well suited to safely preserve ZIP/JAR entries and metadata.
- **Java + ASM** is reserved for explicitly supported compiled-bytecode transformations. It is not a fallback for source transformations: a `.class` file must never be changed by broad text replacement.

## Update guarantees

1. The API uses `PortingEngine.apply_port`; it must not call the old metadata-only writer.
2. Every changed text file is listed in `compatibility-report.json` and returned as `changed_files` to the browser.
3. Missing source code or unsupported bytecode is reported as an unresolved issue, rather than being claimed as ported.
4. Loader-specific metadata overrides are applied by `transformer.rewrite_metadata` from the same archive-writing pipeline.

## Extension points

- Add source rewrite rules in `porting_engine.py` only when they are deterministic and covered by a fixture archive.
- Add mapping-aware rewrites through `planner.py` and feed the exact replacement spans into the engine.
- Add bytecode transformations through the Maven Java project only after validating the exact owner, descriptor, and stack effect.
