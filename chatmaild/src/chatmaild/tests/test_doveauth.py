import io
import json
import queue
import threading
import traceback

import chatmaild.doveauth
import pytest
from chatmaild.doveauth import (
    AuthDictProxy,
    is_allowed_to_create,
    lookup_passdb,
    lookup_userdb,
)
from chatmaild.newemail import create_newemail_dict


def test_basic(example_config):
    lookup_passdb(example_config, "asdf12345@chat.example.org", "q9mr3faue")
    data = lookup_userdb(example_config, "asdf12345@chat.example.org")
    assert data
    data2 = lookup_passdb(
        example_config, "asdf12345@chat.example.org", "q9mr3jewvadsfaue"
    )
    assert data == data2


def test_iterate_addresses(example_config):
    addresses = []

    for i in range(10):
        addresses.append(f"asdf1234{i}@chat.example.org")
        lookup_passdb(example_config, addresses[-1], "q9mr3faue")

    dictproxy = AuthDictProxy(config=example_config)
    res = dictproxy.iter_userdb()
    assert set(res) == set(addresses)


def test_invalid_username_length(example_config):
    config = example_config
    config.username_min_length = 6
    config.username_max_length = 10
    password = create_newemail_dict(config)["password"]
    assert not is_allowed_to_create(config, f"a1234@{config.mail_domain}", password)
    assert is_allowed_to_create(config, f"012345@{config.mail_domain}", password)
    assert is_allowed_to_create(config, f"0123456@{config.mail_domain}", password)
    assert is_allowed_to_create(config, f"0123456789@{config.mail_domain}", password)
    assert not is_allowed_to_create(
        config, f"0123456789x@{config.mail_domain}", password
    )


def test_dont_overwrite_password_on_wrong_login(example_config):
    """Test that logging in with a different password doesn't create a new user"""
    res = lookup_passdb(
        example_config, "newuser12@chat.example.org", "kajdlkajsldk12l3kj1983"
    )
    assert res["password"]
    res2 = lookup_passdb(example_config, "newuser12@chat.example.org", "kajdslqwe")
    # this function always returns a password hash, which is actually compared by dovecot.
    assert res["password"] == res2["password"]


def test_nocreate_file(monkeypatch, tmpdir, example_config):
    p = tmpdir.join("nocreate")
    p.write("")
    monkeypatch.setattr(chatmaild.doveauth, "NOCREATE_FILE", str(p))
    lookup_passdb(example_config, "newuser12@chat.example.org", "zequ0Aimuchoodaechik")
    assert not lookup_userdb(example_config, "newuser12@chat.example.org")


def test_handle_dovecot_request(example_config):
    dictproxy = AuthDictProxy(config=example_config)

    # Test that password can contain ", ', \ and /
    msg = (
        'Lshared/passdb/laksjdlaksjdlak\\\\sjdlk\\"12j\\\'3l1/k2j3123"'
        "some42123@chat.example.org\tsome42123@chat.example.org"
    )
    res = dictproxy.handle_dovecot_request(msg)
    assert res
    assert res[0] == "O" and res.endswith("\n")
    userdata = json.loads(res[1:].strip())
    assert userdata["home"].endswith("chat.example.org/some42123@chat.example.org")
    assert userdata["uid"] == userdata["gid"] == "vmail"
    assert userdata["password"].startswith("{SHA512-CRYPT}")


def test_handle_dovecot_protocol_hello_is_skipped(example_config, caplog):
    dictproxy = AuthDictProxy(config=example_config)
    rfile = io.BytesIO(b"H3\t2\t0\t\tauth\n")
    wfile = io.BytesIO()
    dictproxy.loop_forever(rfile, wfile)
    assert wfile.getvalue() == b""
    assert not caplog.messages


def test_handle_dovecot_protocol_user_not_exists(example_config):
    dictproxy = AuthDictProxy(config=example_config)
    rfile = io.BytesIO(
        b"H3\t2\t0\t\tauth\nLshared/userdb/foobar@chat.example.org\tfoobar@chat.example.org\n"
    )
    wfile = io.BytesIO()
    dictproxy.loop_forever(rfile, wfile)
    assert wfile.getvalue() == b"N\n"


def test_handle_dovecot_protocol_iterate(gencreds, example_config):
    dictproxy = AuthDictProxy(config=example_config)
    lookup_passdb(example_config, "asdf00000@chat.example.org", "q9mr3faue")
    lookup_passdb(example_config, "asdf11111@chat.example.org", "q9mr3faue")
    rfile = io.BytesIO(b"H3\t2\t0\t\tauth\nI0\t0\tshared/userdb/")
    wfile = io.BytesIO()
    dictproxy.loop_forever(rfile, wfile)
    lines = wfile.getvalue().decode("ascii").split("\n")
    assert "Oshared/userdb/asdf00000@chat.example.org\t" in lines
    assert "Oshared/userdb/asdf11111@chat.example.org\t" in lines
    assert not lines[2]


def test_50_concurrent_lookups_different_accounts(gencreds, example_config):
    num_threads = 50
    req_per_thread = 5
    results = queue.Queue()

    def lookup():
        for i in range(req_per_thread):
            addr, password = gencreds()
            try:
                lookup_passdb(example_config, addr, password)
            except Exception:
                results.put(traceback.format_exc())
            else:
                results.put(None)

    threads = []
    for i in range(num_threads):
        thread = threading.Thread(target=lookup, daemon=True)
        threads.append(thread)

    print(f"created {num_threads} threads, starting them and waiting for results")
    for thread in threads:
        thread.start()

    for i in range(num_threads * req_per_thread):
        res = results.get()
        if res is not None:
            pytest.fail(f"concurrent lookup failed\n{res}")
