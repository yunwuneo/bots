def tool(name, description, properties, required=()):
    return {"type": "function", "function": {"name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}}}


S = {"type": "string"}
TOOLS = [
    tool("list_files", "List files in the shared workspace.", {"path": S}),
    tool("read_file", "Read a UTF-8 file in the shared workspace (max 64KB).", {"path": S}, ["path"]),
    tool("write_file", "Save a UTF-8 artifact in the shared workspace. Paths are relative. Existing files are replaced.", {"path": S, "content": S}, ["path", "content"]),
    tool("run_command", "Run a shell command inside the isolated computer, in /workspace. Requires user approval. Do not use it to send messages or publish without explicit authorization.", {"command": S}, ["command"]),
    tool("browser", "Use the persistent browser. Navigate to an HTTP(S) URL, inspect the page, click or fill a CSS selector, or press a key. Inspection returns numbered interactive elements: select them with [data-neobot-id=\"NUMBER\"]. Click, fill and press require approval. Never bypass human verification.", {
        "action": {"type": "string", "enum": ["navigate", "inspect", "click", "fill", "press"]},
        "url": S, "selector": S, "text": S, "key": S}, ["action"]),
    tool("remember", "Replace your durable memory with an updated, concise set of stable facts and preferences. Preserve existing relevant facts. No passwords or secrets.", {"content": S}, ["content"]),
    tool("list_bots", "List teammates available for delegation.", {}),
    tool("delegate", "Ask another Bot to complete a focused task. Your task waits and receives its result. Delegation cycles are rejected.", {"bot_id": S, "task": S}, ["bot_id", "task"]),
    tool("list_skills", "List the shared library of reusable workflow instructions.", {}),
    tool("read_skill", "Read a reusable skill by ID.", {"skill_id": S}, ["skill_id"]),
    tool("save_skill", "Save or update a named skill with steps, inputs, validation and expected output.", {"name": S, "instructions": S}, ["name", "instructions"]),
    tool("create_routine", "Create a recurring task for yourself. Requires user approval. Runs at a fixed interval, minimum 5 minutes. The first run is one interval from now.", {"name": S, "prompt": S, "interval_minutes": {"type": "integer", "minimum": 5, "maximum": 525600}}, ["name", "prompt", "interval_minutes"]),
]


def needs_approval(name, args):
    return name in {"run_command", "create_routine"} or (name == "browser" and args.get("action") not in {"navigate", "inspect"})

