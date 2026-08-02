# Example plugins

Reference reader/writer implementations for `payload` — **not** installed by
default and **not** shipped inside the `payload` pip package (they live outside
`src/`, on purpose). A fresh project has zero readers/writers until you add some.

To use one in a project, install it into that project's `plugins/` folder:

```bash
pld plugin install examples/plugins/raw_text.py
pld plugin install examples/plugins/bin_writer.py
```

or from a raw URL pointing at one of these files (e.g. a raw GitHub link), or
just copy the file by hand — `plugins/*.py` is a plain folder, no packaging
step required. See `src/payload/docs/PLUGINS.md` for the reader/writer contract
these files implement, and to write your own from scratch.

| File | Kind | Notes |
| --- | --- | --- |
| `raw_text.py` | reader | minimal hex-byte text format |
| `csv_reader.py` | reader | structured CSV, multi-byte values |
| `c_source.py` | reader | compiles a `.c` file with a real toolchain (needs `[plugin.c_source]` config) |
| `bin_writer.py` | writer | raw binary dump |
| `hex_writer.py` | writer | Intel HEX |
| `obj_writer.py` | writer | linkable `.o` (needs `[plugin.obj]` config) |
| `header_writer.py` | writer | C header, zero toolchain config |
