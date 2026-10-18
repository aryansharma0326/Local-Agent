"""Tool registry. Each tool has a name, a description and a JSON schema that
the model sees, and a deterministic handler that runs inside the Sandbox.
Handlers return a ToolResult (observation text plus what was read, written
or deleted); they raise AgentError for failures, which the harness turns
into an error observation."""

import csvtools
from errors import AgentError

MAX_FILE_CHARS = 60000   # per file, keeps observations inside Qwen3's 32k context
MAX_TOTAL_CHARS = 70000  # all files of one read_files call together (~28k tokens; CP1 sources are <= 40 KB)

# Names models commonly use for our tools / arguments -> canonical names.
TOOL_ALIASES = {
    "ls": "list_files", "list": "list_files", "list_dir": "list_files", "list_directory": "list_files",
    "read": "read_file", "cat": "read_file", "open_file": "read_file", "view_file": "read_file",
    "read_csv": "read_file", "read_multiple_files": "read_files",
    "write": "write_file", "create_file": "write_file", "create": "write_file", "save_file": "write_file",
    "delete": "delete_file", "remove_file": "delete_file", "remove": "delete_file", "rm": "delete_file",
    "copy": "copy_file", "cp": "copy_file", "duplicate_file": "copy_file", "csv_copy": "copy_file",
    "csv_sum": "csv_aggregate", "csv_total": "csv_aggregate", "csv_average": "csv_aggregate",
    "csv_stats": "csv_aggregate", "aggregate": "csv_aggregate",
    "csv_add_values": "csv_row_sum", "csv_sum_row": "csv_row_sum",
    "csv_update": "csv_update_cell", "csv_set_value": "csv_update_cell", "csv_update_row": "csv_update_cell",
    "csv_remove_rows": "csv_delete_rows", "csv_filter": "csv_delete_rows", "csv_delete": "csv_delete_rows",
    "done": "finish", "final_answer": "finish", "answer": "finish", "respond": "finish", "complete": "finish",
}
ARG_ALIASES = {
    "path": ("file_path", "filepath", "filename", "file_name", "file", "target", "output_path", "csv_path"),
    "paths": ("files", "file_paths", "filepaths", "sources", "source_paths", "path"),
    "content": ("text", "contents", "data", "body", "summary"),
    "content_start": ("start", "from_text", "starts_with", "begin"),
    "content_end": ("end", "to_text", "ends_with", "finish_with"),
    "message": ("answer", "result", "text", "response", "reason"),
    "reasoning": ("explanation", "thoughts", "rationale"),
    "evidence": ("quote", "quotes", "source", "lines", "evidence_lines"),
    "expression": ("expr", "formula", "calculation", "math"),
    "source_path": ("source", "src", "from", "from_path", "input_path", "path"),
    "destination_path": ("destination", "dest", "dst", "to", "to_path", "target_path", "output_path", "copy_path"),
    "output_path": ("output", "save_as", "destination_path", "destination", "out_path", "target_path"),
    "operation": ("op", "function", "aggregate", "agg", "metric"),
    "operator": ("op", "condition", "comparison"),
    "column": ("col", "column_name", "field"),
    "columns": ("cols", "column_names", "fields"),
    "id_column": ("id_col", "key_column", "match_column", "id_field", "key"),
    "id_value": ("id", "row_id", "key_value", "match_value"),
    "new_value": ("value", "new", "set_to", "to"),
}
STATUS_ALIASES = {"ok": "success", "done": "success", "completed": "success", "complete": "success",
                  "succeeded": "success", "fail": "error", "failed": "error", "failure": "error"}
OPERATION_ALIASES = {"total": "sum", "add": "sum", "mean": "average", "avg": "average",
                     "minimum": "min", "maximum": "max", "number": "count"}
_INTERNAL_KEYS = ("aliases", "scalar")  # schema keys used by validation only


class ToolResult:
    def __init__(self, obs, read=(), wrote=None, deleted=None, computed=None, unread=None):
        self.obs = obs
        self.unread = dict(unread or {})  # relpath -> offset where the unshown rest of a long file starts
        self.read = list(read)      # relative paths whose contents were read
        self.wrote = wrote          # (raw_path, content) for a created/overwritten file
        self.deleted = deleted      # raw_path of a deleted file
        self.computed = computed    # (value_text, sentence) of a computed answer


class Tool:
    def __init__(self, name, description, params, handler, required=None, mutates=False):
        self.name = name
        self.description = description
        self.params = params  # {arg_name: JSON-schema fragment}
        self.required = list(required if required is not None else params)
        self.handler = handler
        self.mutates = mutates

    def schema(self):
        props = {k: {a: b for a, b in v.items() if a not in _INTERNAL_KEYS} for k, v in self.params.items()}
        return {"name": self.name, "description": self.description,
                "parameters": {"type": "object", "properties": props,
                               "required": self.required, "additionalProperties": False}}

    def validate(self, args):
        """Check args against the schema; return a cleaned copy or raise ValueError.
        Common synonyms are mapped and harmless type slips are coerced
        (a number answer -> string, a list of lines -> newline-joined text)."""
        args = dict(args)
        for canon, aliases in ARG_ALIASES.items():
            if canon in self.params and canon not in args:
                for a in aliases:
                    if a in args and a not in self.params:
                        args[canon] = args.pop(a)
                        break
        unknown = [k for k in args if k not in self.params]
        if unknown:
            raise ValueError("unknown argument(s) for %s: %s" % (self.name, ", ".join(unknown)))
        clean = {}
        for key, spec in self.params.items():
            if key not in args or args[key] is None:
                if key in self.required:
                    raise ValueError("%s requires argument '%s'" % (self.name, key))
                continue
            val = args[key]
            typ = spec.get("type")
            if typ == "string":
                scalar_ok = spec.get("scalar") or key in ("content", "message")
                if isinstance(val, bool) and spec.get("scalar"):
                    val = "true" if val else "false"
                elif isinstance(val, (int, float)) and not isinstance(val, bool) and scalar_ok:
                    val = str(int(val)) if isinstance(val, float) and val.is_integer() else str(val)
                if key == "content" and isinstance(val, list) and all(isinstance(v, str) for v in val):
                    val = "\n".join(val)
                if not isinstance(val, str):
                    raise ValueError("argument '%s' must be a string" % key)
                if "enum" in spec:
                    val = val.strip().lower()
                    val = spec.get("aliases", {}).get(val, val)
                    if val not in spec["enum"]:
                        raise ValueError("argument '%s' must be one of %s" % (key, spec["enum"]))
            elif typ == "array":
                if isinstance(val, str):
                    val = [val]
                if not isinstance(val, list) or not val or not all(isinstance(v, str) and v.strip() for v in val):
                    raise ValueError("argument '%s' must be a non-empty list of strings" % key)
            clean[key] = val
        return clean


def label_file(path, text):
    return "===== FILE: %s =====\n%s\n===== END FILE =====" % (path, text.rstrip("\n"))


def build_tools(sandbox):
    """Create the tool registry bound to a workspace sandbox."""

    # ------------------------------------------------------------ file tools
    def list_files(args):
        files = sandbox.listing(limit=300)
        return ToolResult("\n".join(files) if files else "(the workspace is empty)")

    def _read(path):
        rel, text = sandbox.read_file(path)
        if len(text) > MAX_FILE_CHARS:
            text = text[:MAX_FILE_CHARS] + "\n[... truncated ...]"
        return rel, text

    def read_file(args):
        rel, text = sandbox.read_file(args["path"])
        try:
            start = max(0, int(str(args.get("offset") or "0").strip()))
        except ValueError:
            raise AgentError("offset must be a whole number of characters")
        if start and start >= len(text):
            raise AgentError("offset %d is past the end of %s (it has %d characters); read it without an offset"
                             % (start, rel, len(text)))
        end = min(len(text), start + MAX_FILE_CHARS)
        part = text[start:end]
        unread = {}
        if start or end < len(text):
            note = "[characters %d-%d of %d shown]" % (start, end, len(text))
            if end < len(text):
                note += (" [THE REST OF THE FILE IS NOT SHOWN: call read_file with \"offset\": \"%d\" to read "
                         "it before concluding that something is missing]" % end)
                unread[rel] = end
            part = part + "\n" + note
        return ToolResult(label_file(rel, part), read=[rel], unread=unread)

    def read_files(args):
        files, seen = [], set()
        for p in args["paths"]:
            rel, text = _read(p)  # any missing file aborts the whole call with an error
            if rel not in seen:
                seen.add(rel)
                files.append((rel, text))
        total = sum(len(t) for _, t in files)
        if total > MAX_TOTAL_CHARS:  # share the budget so every file stays represented
            share = MAX_TOTAL_CHARS // len(files)
            files = [(r, t if len(t) <= share else t[:share] + "\n[... truncated ...]") for r, t in files]
        return ToolResult("\n\n".join(label_file(r, t) for r, t in files), read=[r for r, _ in files])

    def write_file(args):
        rel = sandbox.create_file(args["path"], args["content"])
        return ToolResult("OK: wrote %d characters to %s." % (len(args["content"]), rel),
                          wrote=(args["path"], args["content"]))

    def delete_file(args):
        if set("*?[]") & set(args["path"]):
            raise AgentError("wildcards are not allowed; delete_file removes exactly one named file")
        rel = sandbox.delete_file(args["path"])
        return ToolResult("OK: deleted %s." % rel, deleted=rel)

    def copy_file(args):
        src_rel, data = sandbox.read_bytes(args["source_path"])
        dst_rel = sandbox.normalize(args["destination_path"])
        if dst_rel == src_rel:
            raise AgentError("The copy must have a different path than the original %s." % src_rel)
        sandbox.create_file(dst_rel, data)
        return ToolResult("OK: copied %s to %s (%d bytes, identical content)." % (src_rel, dst_rel, len(data)),
                          read=[src_rel], wrote=(dst_rel, data))

    # ------------------------------------------------------------- CSV tools
    def _table(path):
        rel, text = sandbox.read_file(path)
        return rel, csvtools.Table(text, name=rel)

    def calculate(args):
        value, text = csvtools.evaluate(args["expression"])
        return ToolResult("RESULT: %s = %s" % (args["expression"].strip(), text),
                          computed=(csvtools.fmt_number(value), "%s = %s" % (args["expression"].strip(), text)))

    def csv_aggregate(args):
        rel, table = _table(args["path"])
        value, sentence = csvtools.aggregate(table, args["column"], args["operation"],
                                             args.get("where_column"), args.get("where_value"))
        return ToolResult("RESULT: " + sentence, read=[rel], computed=(value, sentence))

    def csv_row_sum(args):
        rel, table = _table(args["path"])
        value, sentence = csvtools.row_sum(table, args.get("id_column", "id"), args["id_value"], args["columns"])
        return ToolResult("RESULT: " + sentence, read=[rel], computed=(value, sentence))

    def _save_table(rel, args, new_text, sentence):
        out = sandbox.normalize(args.get("output_path") or rel)
        rows = len(csvtools.Table(new_text, name=out).data)
        data = new_text.encode("utf-8")
        if sandbox.read_bytes(rel)[1].startswith(b"\xef\xbb\xbf"):
            data = b"\xef\xbb\xbf" + data  # keep the byte-order mark the original had
        sandbox.create_file(out, data)
        return ToolResult("OK: %s. Saved %d data row(s) plus the header to %s." % (sentence, rows, out),
                          read=[rel], wrote=(out, data))

    def csv_update_cell(args):
        rel, table = _table(args["path"])
        new_text, sentence = csvtools.update_cell(table, args.get("id_column", "id"), args["id_value"],
                                                  args["column"], args["new_value"])
        return _save_table(rel, args, new_text, sentence)

    def csv_delete_rows(args):
        rel, table = _table(args["path"])
        new_text, removed, kept, sentence = csvtools.delete_rows(table, args["column"],
                                                                 args.get("operator", "equals"),
                                                                 args.get("value", ""), args.get("values"),
                                                                 args.get("and_column"), args.get("and_operator"),
                                                                 args.get("and_value"))
        return _save_table(rel, args, new_text, sentence)

    path = {"type": "string", "description": "path relative to the workspace, e.g. 'docs/a.txt'"}
    csv_path = {"type": "string", "description": "path of the CSV file to read"}
    out_path = {"type": "string", "description": "where to save the resulting CSV; omit to save back to the "
                                                 "same file"}
    scalar = {"type": "string", "scalar": True}
    tools = [
        Tool("list_files", "List all files in the workspace (recursive).", {}, list_files),
        Tool("read_file", "Read one text or CSV file. Never modifies it. Long files are shown in parts: pass "
             "offset (a character position) to read the next part.",
             {"path": path, "offset": {"type": "string", "scalar": True,
                                       "description": "optional: character position to start reading from"}},
             read_file, required=["path"]),
        Tool("read_files", "Read several text files at once (use for summaries: pass EVERY source file).",
             {"paths": {"type": "array", "items": {"type": "string"}, "description": "list of relative paths"}},
             read_files),
        Tool("write_file", "Create or overwrite a file with exactly the given content. Parent directories "
             "are created automatically.",
             {"path": path, "content": {"type": "string", "description": "the exact full file content"},
              "content_start": {"type": "string", "description": "for LONG content copied from the request: its "
                                                                "first few words, verbatim (instead of content)"},
              "content_end": {"type": "string", "description": "...and its last few words, verbatim"}},
             write_file, required=["path"], mutates=True),
        Tool("delete_file", "Delete exactly ONE file (never a directory).", {"path": path},
             delete_file, mutates=True),
        Tool("copy_file", "Save an exact, byte-identical copy of a file (e.g. a CSV table) at a new path. "
             "The original is not changed.",
             {"source_path": dict(path, description="existing file to copy"),
              "destination_path": dict(path, description="path of the copy to create")},
             copy_file, mutates=True),
        Tool("csv_aggregate", "Compute the sum (total), average, min, max or count of ONE numeric column of a "
             "CSV file. Exact arithmetic; always use this instead of adding numbers yourself.",
             {"path": csv_path, "column": {"type": "string", "description": "column header name"},
              "operation": {"type": "string", "enum": ["sum", "average", "min", "max", "count"],
                            "aliases": OPERATION_ALIASES},
              "where_column": {"type": "string", "description": "optional: only rows where this column..."},
              "where_value": dict(scalar, description="...equals this value")},
             csv_aggregate, required=["path", "column", "operation"]),
        Tool("csv_row_sum", "In the ONE row identified by an id, add the numeric values of the listed columns.",
             {"path": csv_path, "id_column": {"type": "string", "description": "column that identifies the row, "
                                                                               "e.g. 'id'"},
              "id_value": dict(scalar, description="id of the row"),
              "columns": {"type": "array", "items": {"type": "string"}, "description": "columns to add up"}},
             csv_row_sum, required=["path", "id_value", "columns"]),
        Tool("csv_update_cell", "In the row identified by an id, set one column to a new value and save the "
             "table. All other rows and columns are preserved exactly.",
             {"path": csv_path, "id_column": {"type": "string", "description": "column that identifies the row"},
              "id_value": dict(scalar, description="id of the row to change"),
              "column": {"type": "string", "description": "column to change"},
              "new_value": dict(scalar, description="the new cell value"), "output_path": out_path},
             csv_update_cell, required=["path", "id_value", "column", "new_value"], mutates=True),
        Tool("csv_delete_rows", "Remove every row whose column matches a condition and save the table. The "
             "header and all other rows are preserved exactly.",
             {"path": csv_path, "column": {"type": "string", "description": "column to test"},
              "operator": {"type": "string", "description": "equals (default), not_equals, contains, "
                           "greater_than, greater_or_equal, less_than, less_or_equal, is_empty"},
              "value": dict(scalar, description="value to compare with"),
              "values": {"type": "array", "items": {"type": "string"},
                         "description": "optional: several values; a row matching ANY of them is removed "
                                        "('cancelled or refunded')"},
              "and_column": {"type": "string", "description": "optional second condition that must ALSO hold "
                                                              "('qty is 0 AND status is pending')"},
              "and_operator": {"type": "string", "description": "operator of the second condition"},
              "and_value": dict(scalar, description="value of the second condition"), "output_path": out_path},
             csv_delete_rows, required=["path", "column"], mutates=True),
        Tool("calculate", "Exact arithmetic on numbers copied from a file: + - * / and parentheses, e.g. "
             "'37 + 12 - 4' or '(120 + 180) / 2'. Use it for every total, difference or average.",
             {"expression": {"type": "string", "description": "arithmetic expression using only numbers"}},
             calculate),
        Tool("finish", "End the task. Use status 'success' when done (message = short result, or the full "
             "answer for questions) or 'error' when the task cannot be done (message = why). For a question, "
             "fill evidence and reasoning BEFORE message.",
             {"status": {"type": "string", "enum": ["success", "error"], "aliases": STATUS_ALIASES},
              "evidence": dict(scalar, description="questions only: the exact line(s) copied from the file that "
                                                   "answer the question"),
              "reasoning": dict(scalar, description="questions only: one short sentence linking the evidence to "
                                                    "each part of the question"),
              "message": {"type": "string", "description": "result or answer for the user"}},
             None, required=["status"]),
    ]
    return {t.name: t for t in tools}
