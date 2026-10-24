"""7.1.0 filegator-port regression: files.copy / files.move / files.extract."""
import io
import os
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "04_SOURCE", "HKS_Client"))
from agent.modules import files as F  # noqa: E402


class FakeCtx:
    disconnect_hooks = []


def _mk(root, rel, content=b"hello"):
    fp = os.path.join(root, rel)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "wb") as f:
        f.write(content)
    return fp


def test_copy_move():
    with tempfile.TemporaryDirectory() as root:
        src_dir = os.path.join(root, "src")
        dst_dir = os.path.join(root, "dst")
        os.makedirs(src_dir); os.makedirs(dst_dir)
        _mk(src_dir, "a.txt")
        os.makedirs(os.path.join(src_dir, "sub"))
        _mk(src_dir, os.path.join("sub", "deep.txt"), b"deep")
        reg = F.register(FakeCtx())
        out = reg["files.copy"](FakeCtx(), {"paths": [os.path.join(src_dir, "a.txt"),
                                                      os.path.join(src_dir, "sub")], "dest": dst_dir})
        assert len(out["copied"]) == 2 and not out["failed"], out
        assert os.path.isfile(os.path.join(dst_dir, "a.txt"))
        assert os.path.isdir(os.path.join(dst_dir, "sub"))
        # copy again -> no overwrite, unique name
        out2 = reg["files.copy"](FakeCtx(), {"paths": [os.path.join(src_dir, "a.txt")], "dest": dst_dir})
        assert out2["copied"][0].endswith("a (1).txt"), out2
        # move
        out3 = reg["files.move"](FakeCtx(), {"paths": [os.path.join(dst_dir, "a (1).txt")], "dest": src_dir})
        assert len(out3["moved"]) == 1 and not out3["failed"], out3
        assert os.path.isfile(os.path.join(src_dir, "a (1).txt"))
        assert not os.path.exists(os.path.join(dst_dir, "a (1).txt"))
        # missing source -> per-item failure, no crash
        out4 = reg["files.move"](FakeCtx(), {"paths": [os.path.join(src_dir, "ghost.txt")], "dest": dst_dir})
        assert not out4["moved"] and out4["failed"][0]["error"]
    print("  files: copy/move + unique names + per-item errors OK")


def test_extract_zipslip():
    with tempfile.TemporaryDirectory() as root:
        reg = F.register(FakeCtx())
        zpath = os.path.join(root, "good.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("in/a.txt", "x")
            z.writestr("in/b.txt", "y")
        out = reg["files.extract"](FakeCtx(), {"path": zpath})
        assert out["files"] == 2 and os.path.isfile(os.path.join(out["extracted"], "in", "a.txt")), out
        # zip-slip attempt must be refused
        bad = os.path.join(root, "bad.zip")
        with zipfile.ZipFile(bad, "w") as z:
            z.writestr("../evil.txt", "x")
        out2 = reg["files.extract"](FakeCtx(), {"path": bad})
        assert not out2.get("ok", True) and "unsafe path" in out2["error"], out2
        assert not os.path.exists(os.path.join(os.path.dirname(root), "evil.txt"))
    print("  files: extract + zip-slip guard OK")


if __name__ == "__main__":
    test_copy_move()
    test_extract_zipslip()
    print("FILE-OPS TESTS PASSED")
