import os

content = r"""import os
import pytest
import asyncio
from neurocore.tools.file_tools import (
    ReadFileTool,
    WriteFileTool,
    ReplaceFileContentTool,
    ListDirTool,
    GrepSearchTool,
)
from neurocore.tools.shell_tools import RunCommandTool
from neurocore.tools.executor import ToolExecutor

@pytest.fixture
def workspace_dir(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    return str(ws)

# ============================================================================
# 1. ReadFileTool Tests (Tier 1 & Tier 2)
# ============================================================================

# Tier 1
def test_read_file_success(workspace_dir):
    file_path = os.path.join(workspace_dir, "sample.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n")

    tool = ReadFileTool(workspace_root=workspace_dir)
    res = tool.run(path="sample.txt")
    assert res.status == "success"
    assert "Line 1" in str(res.output)
    assert "Line 5" in str(res.output)

def test_read_file_line_slice(workspace_dir):
    file_path = os.path.join(workspace_dir, "sample.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n")

    tool = ReadFileTool(workspace_root=workspace_dir)
    res = tool.run(path="sample.txt", start_line=2, end_line=3)
    assert res.status == "success"
    assert "2: Line 2" in str(res.output)
    assert "3: Line 3" in str(res.output)
    assert "Line 4" not in str(res.output)

def test_read_file_full_content_without_slice(workspace_dir):
    file_path = os.path.join(workspace_dir, "full.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("Hello World\nSecond Line")

    tool = ReadFileTool(workspace_root=workspace_dir)
    res = tool.run(path="full.txt")
    assert res.status == "success"
    assert res.output == "Hello World\nSecond Line"

def test_read_file_relative_path(workspace_dir):
    sub = os.path.join(workspace_dir, "sub")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "rel.txt"), "w", encoding="utf-8") as f:
        f.write("Relative file content")

    tool = ReadFileTool(workspace_root=workspace_dir)
    res = tool.run(path="sub/rel.txt")
    assert res.status == "success"
    assert "Relative file content" in res.output

def test_read_file_multiline_utf8(workspace_dir):
    file_path = os.path.join(workspace_dir, "unicode.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("✨ NeuroCore 🚀\nこんにちは世界\n")

    tool = ReadFileTool(workspace_root=workspace_dir)
    res = tool.run(path="unicode.txt")
    assert res.status == "success"
    assert "✨ NeuroCore 🚀" in res.output

# Tier 2
def test_read_file_not_found(workspace_dir):
    tool = ReadFileTool(workspace_root=workspace_dir)
    res = tool.run(path="non_existent.txt")
    assert res.status == "error"
    assert "File not found" in res.error

def test_read_file_binary_guard(workspace_dir):
    for ext in [".png", ".pdf", ".pyc", ".zip", ".exe"]:
        binary_path = os.path.join(workspace_dir, f"file{ext}")
        with open(binary_path, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")

        tool = ReadFileTool(workspace_root=workspace_dir)
        res = tool.run(path=f"file{ext}")
        assert res.status == "error"
        assert "binary" in res.error.lower()

def test_read_file_unicode_decode_error(workspace_dir):
    file_path = os.path.join(workspace_dir, "corrupt.txt")
    with open(file_path, "wb") as f:
        f.write(b"\xff\xfe\xfd\x80\x81")

    tool = ReadFileTool(workspace_root=workspace_dir)
    res = tool.run(path="corrupt.txt")
    assert res.status == "error"
    assert "binary" in res.error.lower() or "utf-8" in res.error.lower()

def test_read_file_path_traversal_guard(workspace_dir):
    tool = ReadFileTool(workspace_root=workspace_dir)
    res1 = tool.run(path="../../etc/passwd")
    assert res1.status == "error"
    assert "access denied" in res1.error.lower() or "workspace" in res1.error.lower()

    res2 = tool.run(path="/etc/passwd")
    assert res2.status == "error"
    assert "access denied" in res2.error.lower() or "workspace" in res2.error.lower()

def test_read_file_directory_target(workspace_dir):
    dir_path = os.path.join(workspace_dir, "dir_target")
    os.makedirs(dir_path, exist_ok=True)

    tool = ReadFileTool(workspace_root=workspace_dir)
    res = tool.run(path="dir_target")
    assert res.status == "error"
    assert "directory" in res.error.lower()

def test_read_file_exceed_max_size(workspace_dir, monkeypatch):
    file_path = os.path.join(workspace_dir, "big.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("X" * 200)

    tool = ReadFileTool(workspace_root=workspace_dir)
    class FakeSettings:
        max_file_size_bytes = 100
    monkeypatch.setattr("neurocore.tools.file_tools.get_settings", lambda: FakeSettings())

    res = tool.run(path="big.txt")
    assert res.status == "error"
    assert "exceeds limit" in res.error.lower()

def test_read_file_invalid_line_slice_bounds(workspace_dir):
    file_path = os.path.join(workspace_dir, "short.txt")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write("Only one line\n")

    tool = ReadFileTool(workspace_root=workspace_dir)
    res = tool.run(path="short.txt", start_line=10, end_line=20)
    assert res.status == "success"
    assert res.output == ""

# ============================================================================
# 2. WriteFileTool Tests (Tier 1 & Tier 2)
# ============================================================================

# Tier 1
def test_write_file_create(workspace_dir):
    tool = WriteFileTool(workspace_root=workspace_dir)
    res = tool.run(path="nested/dir/test.py", content="print('hello')\n")
    assert res.status == "success"
    assert os.path.exists(os.path.join(workspace_dir, "nested/dir/test.py"))
    with open(os.path.join(workspace_dir, "nested/dir/test.py"), "r") as f:
        assert f.read() == "print('hello')\n"

def test_write_file_overwrite_flag(workspace_dir):
    file_path = os.path.join(workspace_dir, "file.txt")
    with open(file_path, "w") as f:
        f.write("original")

    tool = WriteFileTool(workspace_root=workspace_dir)
    res_fail = tool.run(path="file.txt", content="new content", overwrite=False)
    assert res_fail.status == "error"
    assert "already exists" in res_fail.error.lower()

    res_ok = tool.run(path="file.txt", content="new content", overwrite=True)
    assert res_ok.status == "success"
    with open(file_path, "r") as f:
        assert f.read() == "new content"

def test_write_file_empty_content(workspace_dir):
    tool = WriteFileTool(workspace_root=workspace_dir)
    res = tool.run(path="empty.txt", content="", overwrite=True)
    assert res.status == "success"
    assert res.output["bytes_written"] == 0
    assert os.path.getsize(os.path.join(workspace_dir, "empty.txt")) == 0

def test_write_file_multiline_code(workspace_dir):
    content = "import sys\n\ndef main():\n    print('Running NeuroCore')\n\nif __name__ == '__main__':\n    main()\n"
    tool = WriteFileTool(workspace_root=workspace_dir)
    res = tool.run(path="app.py", content=content)
    assert res.status == "success"
    with open(os.path.join(workspace_dir, "app.py"), "r") as f:
        assert f.read() == content

def test_write_file_returns_correct_metadata(workspace_dir):
    tool = WriteFileTool(workspace_root=workspace_dir)
    res = tool.run(path="meta.txt", content="Hello metadata")
    assert res.status == "success"
    assert res.output["path"] == "meta.txt"
    assert res.output["bytes_written"] == len("Hello metadata".encode("utf-8"))
    assert res.output["status"] == "written"

# Tier 2
def test_write_file_path_traversal_guard(workspace_dir):
    tool = WriteFileTool(workspace_root=workspace_dir)
    res = tool.run(path="../../../tmp/evil.txt", content="evil", overwrite=True)
    assert res.status == "error"
    assert "access denied" in res.error.lower() or "workspace" in res.error.lower()

def test_write_file_special_characters_utf8(workspace_dir):
    content = "Special symbols: ✨ NeuroCore 🔥 #$%^&*()[]{}"
    tool = WriteFileTool(workspace_root=workspace_dir)
    res = tool.run(path="special.txt", content=content)
    assert res.status == "success"
    with open(os.path.join(workspace_dir, "special.txt"), "r", encoding="utf-8") as f:
        assert f.read() == content

def test_write_file_overwrite_false_error(workspace_dir):
    tool = WriteFileTool(workspace_root=workspace_dir)
    tool.run(path="existing.txt", content="initial")
    res = tool.run(path="existing.txt", content="second", overwrite=False)
    assert res.status == "error"
    assert "overwrite=false" in res.error.lower()

# ============================================================================
# 3. ReplaceFileContentTool Tests (Tier 1 & Tier 2)
# ============================================================================

# Tier 1
def test_replace_file_content_success(workspace_dir):
    file_path = os.path.join(workspace_dir, "code.py")
    with open(file_path, "w") as f:
        f.write("def foo():\n    return 1\n")

    tool = ReplaceFileContentTool(workspace_root=workspace_dir)
    res = tool.run(path="code.py", target_content="return 1", replacement_content="return 42")
    assert res.status == "success"
    assert isinstance(res.output, dict)
    assert "diff" in res.output
    with open(file_path, "r") as f:
        assert "return 42" in f.read()

def test_replace_file_content_alias_kwargs(workspace_dir):
    file_path = os.path.join(workspace_dir, "alias.py")
    with open(file_path, "w") as f:
        f.write("old_val = 10\n")

    tool = ReplaceFileContentTool(workspace_root=workspace_dir)
    res = tool.run(path="alias.py", old_content="old_val = 10", new_content="new_val = 20")
    assert res.status == "success"
    with open(file_path, "r") as f:
        assert "new_val = 20" in f.read()

def test_replace_file_content_multiline_block(workspace_dir):
    file_path = os.path.join(workspace_dir, "block.py")
    old_block = "def add(a, b):\n    return a + b"
    new_block = "def add(a, b):\n    # docstring\n    return a + b"
    with open(file_path, "w") as f:
        f.write(old_block + "\n")

    tool = ReplaceFileContentTool(workspace_root=workspace_dir)
    res = tool.run(path="block.py", target_content=old_block, replacement_content=new_block)
    assert res.status == "success"
    with open(file_path, "r") as f:
        assert new_block in f.read()

def test_replace_file_content_exact_match(workspace_dir):
    file_path = os.path.join(workspace_dir, "exact.txt")
    with open(file_path, "w") as f:
        f.write("alpha\nbeta\ngamma\n")

    tool = ReplaceFileContentTool(workspace_root=workspace_dir)
    res = tool.run(path="exact.txt", target_content="beta", replacement_content="DELTA")
    assert res.status == "success"
    with open(file_path, "r") as f:
        assert f.read() == "alpha\nDELTA\ngamma\n"

def test_replace_file_content_diff_format(workspace_dir):
    file_path = os.path.join(workspace_dir, "diff_test.txt")
    with open(file_path, "w") as f:
        f.write("line1\nline2\nline3\n")

    tool = ReplaceFileContentTool(workspace_root=workspace_dir)
    res = tool.run(path="diff_test.txt", target_content="line2", replacement_content="line2_mod")
    assert res.status == "success"
    diff = res.output["diff"]
    assert "-line2" in diff
    assert "+line2_mod" in diff

# Tier 2
def test_replace_file_content_non_unique(workspace_dir):
    file_path = os.path.join(workspace_dir, "code.py")
    with open(file_path, "w") as f:
        f.write("foo = 1\nfoo = 1\n")

    tool = ReplaceFileContentTool(workspace_root=workspace_dir)
    res = tool.run(path="code.py", target_content="foo = 1", replacement_content="foo = 2")
    assert res.status == "error"
    assert "multiple" in res.error.lower() or "unique" in res.error.lower()

def test_replace_file_content_not_found(workspace_dir):
    file_path = os.path.join(workspace_dir, "code.py")
    with open(file_path, "w") as f:
        f.write("foo = 1\n")

    tool = ReplaceFileContentTool(workspace_root=workspace_dir)
    res = tool.run(path="code.py", target_content="bar = 2", replacement_content="bar = 3")
    assert res.status == "error"
    assert "not found" in res.error.lower()

def test_replace_file_content_file_not_found(workspace_dir):
    tool = ReplaceFileContentTool(workspace_root=workspace_dir)
    res = tool.run(path="missing.py", target_content="a", replacement_content="b")
    assert res.status == "error"
    assert "file not found" in res.error.lower()

def test_replace_file_content_path_traversal_guard(workspace_dir):
    tool = ReplaceFileContentTool(workspace_root=workspace_dir)
    res = tool.run(path="../../etc/hosts", target_content="127.0.0.1", replacement_content="0.0.0.0")
    assert res.status == "error"
    assert "access denied" in res.error.lower() or "workspace" in res.error.lower()

def test_replace_file_content_empty_target(workspace_dir):
    file_path = os.path.join(workspace_dir, "empty_target.txt")
    with open(file_path, "w") as f:
        f.write("content")

    tool = ReplaceFileContentTool(workspace_root=workspace_dir)
    res = tool.run(path="empty_target.txt", target_content="nonexistent", replacement_content="something")
    assert res.status == "error"

# ============================================================================
# 4. ListDirTool Tests (Tier 1 & Tier 2)
# ============================================================================

# Tier 1
def test_list_dir_success(workspace_dir):
    os.makedirs(os.path.join(workspace_dir, "subdir"), exist_ok=True)
    with open(os.path.join(workspace_dir, "file.txt"), "w") as f:
        f.write("test content")

    tool = ListDirTool(workspace_root=workspace_dir)
    res = tool.run(path=".")
    assert res.status == "success"
    assert isinstance(res.output, list)
    names = [item["name"] for item in res.output]
    assert "subdir" in names
    assert "file.txt" in names

def test_list_dir_sorting(workspace_dir):
    os.makedirs(os.path.join(workspace_dir, "z_dir"), exist_ok=True)
    os.makedirs(os.path.join(workspace_dir, "a_dir"), exist_ok=True)
    with open(os.path.join(workspace_dir, "a_file.txt"), "w") as f:
        f.write("1")
    with open(os.path.join(workspace_dir, "z_file.txt"), "w") as f:
        f.write("2")

    tool = ListDirTool(workspace_root=workspace_dir)
    res = tool.run(path=".")
    assert res.status == "success"
    names = [item["name"] for item in res.output]
    types = [item["type"] for item in res.output]
    assert names[:2] == ["a_dir", "z_dir"]
    assert types[:2] == ["directory", "directory"]

def test_list_dir_default_path(workspace_dir):
    tool = ListDirTool(workspace_root=workspace_dir)
    res = tool.run()
    assert res.status == "success"
    assert isinstance(res.output, list)

def test_list_dir_nested_directory(workspace_dir):
    sub = os.path.join(workspace_dir, "level1", "level2")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "deep.txt"), "w") as f:
        f.write("deep")

    tool = ListDirTool(workspace_root=workspace_dir)
    res = tool.run(path="level1/level2")
    assert res.status == "success"
    assert res.output[0]["name"] == "deep.txt"

def test_list_dir_metadata(workspace_dir):
    with open(os.path.join(workspace_dir, "meta.txt"), "w") as f:
        f.write("12345")

    tool = ListDirTool(workspace_root=workspace_dir)
    res = tool.run(path=".")
    assert res.status == "success"
    item = next(i for i in res.output if i["name"] == "meta.txt")
    assert item["type"] == "file"
    assert item["size"] == 5
    assert "modified_time" in item

# Tier 2
def test_list_dir_not_found(workspace_dir):
    tool = ListDirTool(workspace_root=workspace_dir)
    res = tool.run(path="ghost_folder")
    assert res.status == "error"
    assert "directory not found" in res.error.lower()

def test_list_dir_file_target(workspace_dir):
    file_path = os.path.join(workspace_dir, "not_a_dir.txt")
    with open(file_path, "w") as f:
        f.write("hello")

    tool = ListDirTool(workspace_root=workspace_dir)
    res = tool.run(path="not_a_dir.txt")
    assert res.status == "error"
    assert "not a directory" in res.error.lower()

def test_list_dir_path_traversal_guard(workspace_dir):
    tool = ListDirTool(workspace_root=workspace_dir)
    res = tool.run(path="../../..")
    assert res.status == "error"
    assert "access denied" in res.error.lower() or "workspace" in res.error.lower()

def test_list_dir_empty_directory(workspace_dir):
    empty_sub = os.path.join(workspace_dir, "empty")
    os.makedirs(empty_sub, exist_ok=True)

    tool = ListDirTool(workspace_root=workspace_dir)
    res = tool.run(path="empty")
    assert res.status == "success"
    assert res.output == []

# ============================================================================
# 5. GrepSearchTool Tests (Tier 1 & Tier 2)
# ============================================================================

# Tier 1
def test_grep_search_success(workspace_dir):
    with open(os.path.join(workspace_dir, "a.py"), "w") as f:
        f.write("def target_function(): pass\n")
    with open(os.path.join(workspace_dir, "b.py"), "w") as f:
        f.write("def other_function(): pass\n")

    tool = GrepSearchTool(workspace_root=workspace_dir)
    res = tool.run(pattern="target_function", path=".")
    assert res.status == "success"
    assert len(res.output) == 1
    assert "a.py" in res.output[0]["file"]

def test_grep_search_regex(workspace_dir):
    with open(os.path.join(workspace_dir, "regex.txt"), "w") as f:
        f.write("user_123 = active\nuser_456 = inactive\n")

    tool = GrepSearchTool(workspace_root=workspace_dir)
    res = tool.run(pattern=r"user_\d+", path=".", is_regex=True)
    assert res.status == "success"
    assert len(res.output) == 2

def test_grep_search_case_insensitive(workspace_dir):
    with open(os.path.join(workspace_dir, "case.txt"), "w") as f:
        f.write("NEUROCORE ENGINE\nneurocore agent\n")

    tool = GrepSearchTool(workspace_root=workspace_dir)
    res = tool.run(pattern="neurocore", path=".", case_sensitive=False)
    assert res.status == "success"
    assert len(res.output) == 2

def test_grep_search_specific_path(workspace_dir):
    sub = os.path.join(workspace_dir, "sub")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "target.py"), "w") as f:
        f.write("SECRET_KEY = 123\n")

    tool = GrepSearchTool(workspace_root=workspace_dir)
    res = tool.run(pattern="SECRET_KEY", path="sub")
    assert res.status == "success"
    assert len(res.output) == 1

def test_grep_search_single_file(workspace_dir):
    with open(os.path.join(workspace_dir, "single.py"), "w") as f:
        f.write("import os\nimport sys\n")

    tool = GrepSearchTool(workspace_root=workspace_dir)
    res = tool.run(pattern="sys", path="single.py")
    assert res.status == "success"
    assert len(res.output) == 1
    assert res.output[0]["line_number"] == 2

# Tier 2
def test_grep_search_no_matches(workspace_dir):
    with open(os.path.join(workspace_dir, "a.py"), "w") as f:
        f.write("def target_function(): pass\n")

    tool = GrepSearchTool(workspace_root=workspace_dir)
    res = tool.run(pattern="non_existent_pattern", path=".")
    assert res.status == "success"
    assert len(res.output) == 0

def test_grep_search_path_not_found(workspace_dir):
    tool = GrepSearchTool(workspace_root=workspace_dir)
    res = tool.run(pattern="test", path="non_existent_dir")
    assert res.status == "error"
    assert "not found" in res.error.lower()

def test_grep_search_path_traversal_guard(workspace_dir):
    tool = GrepSearchTool(workspace_root=workspace_dir)
    res = tool.run(pattern="root", path="../../..")
    assert res.status == "error"
    assert "access denied" in res.error.lower() or "workspace" in res.error.lower()

def test_grep_search_ignores_hidden_dirs(workspace_dir):
    git_dir = os.path.join(workspace_dir, ".git")
    os.makedirs(git_dir, exist_ok=True)
    with open(os.path.join(git_dir, "HEAD"), "w") as f:
        f.write("ref: refs/heads/main\n")

    tool = GrepSearchTool(workspace_root=workspace_dir)
    res = tool.run(pattern="refs/heads", path=".")
    assert res.status == "success"
    assert len(res.output) == 0

# ============================================================================
# 6. RunCommandTool Tests (Tier 1 & Tier 2)
# ============================================================================

# Tier 1
@pytest.mark.asyncio
async def test_run_command_success(workspace_dir):
    tool = RunCommandTool(workspace_root=workspace_dir)
    res = await tool.run_async(command="echo 'NeuroCore Test'")
    assert res.status == "success"
    assert res.output["exit_code"] == 0
    assert "NeuroCore Test" in res.output["stdout"]

@pytest.mark.asyncio
async def test_run_command_cwd(workspace_dir):
    sub = os.path.join(workspace_dir, "subdir")
    os.makedirs(sub, exist_ok=True)
    tool = RunCommandTool(workspace_root=workspace_dir)
    res = await tool.run_async(command="pwd", cwd="subdir")
    assert res.status == "success"
    assert "subdir" in res.output["stdout"]

@pytest.mark.asyncio
async def test_run_command_nonzero_exit(workspace_dir):
    tool = RunCommandTool(workspace_root=workspace_dir)
    res = await tool.run_async(command="ls /non_existent_directory_xyz")
    assert res.status == "error"
    assert res.output["exit_code"] != 0

@pytest.mark.asyncio
async def test_run_command_multiline_stdout(workspace_dir):
    tool = RunCommandTool(workspace_root=workspace_dir)
    res = await tool.run_async(command="python3 -c 'print(1); print(2); print(3)'")
    assert res.status == "success"
    assert "1\n2\n3" in res.output["stdout"]

def test_run_command_sync_run(workspace_dir):
    tool = RunCommandTool(workspace_root=workspace_dir)
    res = tool.run(command="echo 'Sync Execution'")
    assert res.status == "success"
    assert "Sync Execution" in res.output["stdout"]

# Tier 2
@pytest.mark.asyncio
async def test_run_command_prohibited_rm_rf(workspace_dir):
    tool = RunCommandTool(workspace_root=workspace_dir)
    res = await tool.run_async(command="rm -rf /")
    assert res.status == "error"
    assert "prohibited" in res.error.lower() or "blocked" in res.error.lower()

@pytest.mark.asyncio
async def test_run_command_prohibited_mkfs(workspace_dir):
    tool = RunCommandTool(workspace_root=workspace_dir)
    res = await tool.run_async(command="mkfs.ext4 /dev/sdb")
    assert res.status == "error"

@pytest.mark.asyncio
async def test_run_command_prohibited_fork_bomb(workspace_dir):
    tool = RunCommandTool(workspace_root=workspace_dir)
    res = await tool.run_async(command=":(){ :|:& };:")
    assert res.status == "error"

@pytest.mark.asyncio
async def test_run_command_prohibited_dd(workspace_dir):
    tool = RunCommandTool(workspace_root=workspace_dir)
    res = await tool.run_async(command="dd if=/dev/zero of=/dev/sda")
    assert res.status == "error"

@pytest.mark.asyncio
async def test_run_command_timeout(workspace_dir):
    tool = RunCommandTool(workspace_root=workspace_dir)
    res = await tool.run_async(command="sleep 5", timeout=1)
    assert res.status == "error"
    assert "timed out" in res.error.lower()

@pytest.mark.asyncio
async def test_run_command_invalid_cwd(workspace_dir):
    tool = RunCommandTool(workspace_root=workspace_dir)
    res = await tool.run_async(command="ls", cwd="missing_dir")
    assert res.status == "error"
    assert "invalid working directory" in res.error.lower()

@pytest.mark.asyncio
async def test_run_command_stdout_truncation(workspace_dir):
    tool = RunCommandTool(workspace_root=workspace_dir)
    cmd = "python3 -c 'print(\"A\" * 60000)'"
    res = await tool.run_async(command=cmd)
    assert res.status == "success"
    assert "[Truncated stdout]" in res.output["stdout"]

# ============================================================================
# 7. ToolExecutor Tests (Tier 1 & Tier 2)
# ============================================================================

# Tier 1
@pytest.mark.asyncio
async def test_tool_executor_dispatch_write(workspace_dir):
    executor = ToolExecutor(workspace_root=workspace_dir)
    res = await executor.execute(
        tool_call_id="call_123",
        name="write_file",
        arguments={"path": "exec_test.txt", "content": "Hello Executor"}
    )
    assert res.status == "success"
    assert res.tool_call_id == "call_123"
    assert os.path.exists(os.path.join(workspace_dir, "exec_test.txt"))

@pytest.mark.asyncio
async def test_tool_executor_dispatch_read(workspace_dir):
    with open(os.path.join(workspace_dir, "read.txt"), "w") as f:
        f.write("Read me")

    executor = ToolExecutor(workspace_root=workspace_dir)
    res = await executor.execute(
        tool_call_id="call_456",
        name="read_file",
        arguments={"path": "read.txt"}
    )
    assert res.status == "success"
    assert res.output == "Read me"

def test_tool_executor_manifest(workspace_dir):
    executor = ToolExecutor(workspace_root=workspace_dir)
    manifest = executor.get_tools_manifest()
    assert isinstance(manifest, dict)
    assert "read_file" in manifest or "tools" in manifest

def test_tool_executor_list_tools(workspace_dir):
    executor = ToolExecutor(workspace_root=workspace_dir)
    tools = executor.tools
    assert "read_file" in tools
    assert "write_file" in tools
    assert "run_command" in tools

# Tier 2
@pytest.mark.asyncio
async def test_tool_executor_unknown_tool(workspace_dir):
    executor = ToolExecutor(workspace_root=workspace_dir)
    res = await executor.execute(
        tool_call_id="call_err",
        name="unknown_magic_tool",
        arguments={}
    )
    assert res.status == "error"
    assert "unknown" in res.error.lower() or "not found" in res.error.lower()

@pytest.mark.asyncio
async def test_tool_executor_missing_arguments(workspace_dir):
    executor = ToolExecutor(workspace_root=workspace_dir)
    res = await executor.execute(
        tool_call_id="call_bad_args",
        name="read_file",
        arguments={}
    )
    assert res.status == "error"
"""

with open("tests/test_tools.py", "w", encoding="utf-8") as f:
    f.write(content)
print("Updated tests/test_tools.py")
