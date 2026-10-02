"""密码哈希回归测试 —— 防止 passlib / bcrypt 72 字节问题复发.

背景（生产事故）:
    passlib 1.7.4 初始化 bcrypt 后端时会调用 detect_wrap_bug()，
    该函数故意传入 >72 字节的密码；bcrypt 4.x 起对超长密码抛
    ValueError 而非静默截断，导致 passlib 后端加载失败 →
    应用启动即崩溃、容器反复重启（gunicorn "Worker failed to boot."）。

    现 app/core/security.py 已改用 bcrypt 直连，本测试锁定该修复。

运行:
    python backend/tests/test_password_hash.py
"""
import os
import sys

import bcrypt

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from app.core.security import (          # noqa: E402
    hash_password, verify_password, _to_bcrypt_bytes, _BCRYPT_MAX_BYTES,
)


def test_roundtrip():
    h = hash_password("admin_test")
    assert h.startswith("$2b$"), f"哈希格式应为 bcrypt $2b$，实际 {h[:8]}"
    assert verify_password("admin_test", h) is True
    assert verify_password("wrong_password", h) is False
    assert verify_password("", h) is False
    print(f"  [OK] roundtrip: {h[:29]}...")


def test_ascii_over_72_bytes_no_crash():
    """超过 72 字节的 ASCII 密码不能抛异常（旧 bug 的触发条件）."""
    long_pwd = "a" * 200
    h = hash_password(long_pwd)                      # 旧代码在这里崩溃
    assert verify_password(long_pwd, h) is True
    # 前 72 字节相同 → 视为同一密码（与 passlib 旧行为一致）
    assert verify_password("a" * 72, h) is True
    assert verify_password("a" * 71 + "b", h) is False
    print("  [OK] 200 字节 ASCII 密码不崩溃，且按前 72 字节语义匹配")


def test_chinese_over_72_bytes_no_crash():
    """中文密码按 UTF-8 是 3 字节/字，24 字即超 72 字节——必须按字节截断."""
    cn_pwd = "证件照系统安全密码" * 4          # 36 字 × 3 = 108 字节
    assert len(cn_pwd.encode("utf-8")) > _BCRYPT_MAX_BYTES, "测试用例本身应超 72 字节"
    h = hash_password(cn_pwd)
    assert verify_password(cn_pwd, h) is True
    # 若按字符截断会切坏 UTF-8，这里必须是字节截断
    assert _to_bcrypt_bytes(cn_pwd) == cn_pwd.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    print(f"  [OK] 中文密码 {len(cn_pwd)} 字 / "
          f"{len(cn_pwd.encode('utf-8'))} 字节，按字节截断正常")


def test_passlib_era_hash_still_verifies():
    """历史 passlib 生成的哈希必须仍能验证（老数据零迁移）.

    passlib 的 bcrypt 行为 = 「明文截断到 72 字节 → gensalt(rounds=12) → hashpw」，
    这里用 bcrypt 直接复现该产物，再交给新的 verify_password 校验。
    """
    legacy_pwd = "legacy_pass_超过七十二字节" * 3
    legacy_hash = bcrypt.hashpw(
        legacy_pwd.encode("utf-8")[:_BCRYPT_MAX_BYTES],
        bcrypt.gensalt(rounds=12),
    ).decode("utf-8")

    assert verify_password(legacy_pwd, legacy_hash) is True, \
        "旧 passlib 哈希验不过 —— 会导致所有老账号无法登录！"
    assert verify_password("not_it", legacy_hash) is False
    print(f"  [OK] passlib 时代哈希兼容: {legacy_hash[:29]}...")


def test_malformed_hash_returns_false_not_raise():
    """脏数据/非法哈希必须返回 False，不能把异常抛给接口层（否则 500）."""
    for bad in ("", "not-a-hash", "$2b$12$tooshort", "$2b$12$" + "x" * 100, "???", None):
        assert verify_password("any", bad) is False, f"非法哈希 {bad!r} 应返回 False"
    print("  [OK] 非法哈希一律返回 False，不抛异常")


def test_passlib_not_imported():
    """回归护栏：确保没人再把 passlib 引回来."""
    assert "passlib" not in sys.modules, "passlib 不应被导入（已弃用，会引发启动崩溃）"
    import app.core.security as sec
    assert not hasattr(sec, "pwd_context"), "security 模块不应再有 passlib 的 pwd_context"
    print("  [OK] passlib 未被导入，pwd_context 已移除")


def test_default_admin_password_within_limit():
    """默认管理员密码必须在 72 字节内（中国区部署尤其注意）."""
    from app.core.admin_seed import DEFAULT_ADMIN_PASSWORD
    n = len(DEFAULT_ADMIN_PASSWORD.encode("utf-8"))
    assert n <= _BCRYPT_MAX_BYTES, f"默认管理员密码 {n} 字节，超过 bcrypt 上限"
    h = hash_password(DEFAULT_ADMIN_PASSWORD)
    assert verify_password(DEFAULT_ADMIN_PASSWORD, h) is True
    print(f"  [OK] 默认管理员密码 {DEFAULT_ADMIN_PASSWORD!r}（{n} 字节）可正常哈希/校验")


if __name__ == "__main__":
    print("=== 密码哈希回归测试（passlib/bcrypt 事故防护） ===")
    for fn in (
        test_roundtrip,
        test_ascii_over_72_bytes_no_crash,
        test_chinese_over_72_bytes_no_crash,
        test_passlib_era_hash_still_verifies,
        test_malformed_hash_returns_false_not_raise,
        test_passlib_not_imported,
        test_default_admin_password_within_limit,
    ):
        print(f"[{fn.__name__}]")
        fn()
    print("\n全部通过 ✅")
