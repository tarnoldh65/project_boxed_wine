import pytest

from app.observables import (
    Candidate,
    ObservableType,
    defang,
    extract,
    normalize,
    refang,
    strings_in,
)

T = ObservableType
MD5 = "44d88612fea8a8f36de82e1278abb02f"
SHA1 = "3395856ce81f2b7382dee72602f798b642f14140"
SHA256 = "275a021bbfb6489e54d471899f7db9d1663fc695ec2fe2a2c4538aabf651fd0f"


def found(text: str) -> list[tuple[str, str]]:
    return [(c.type.value, c.value) for c in extract(text)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # True positives, one type at a time
        ("beacon to 203.0.113.7 seen", [("ipv4", "203.0.113.7")]),
        ("src=10.0.0.1, dst=10.0.0.2.", [("ipv4", "10.0.0.1"), ("ipv4", "10.0.0.2")]),
        ("v6 2001:db8::1 here", [("ipv6", "2001:db8::1")]),
        ("full 2001:0db8:0000:0000:0000:ff00:0042:8329", [("ipv6", "2001:db8::ff00:42:8329")]),
        ("loopback ::1", [("ipv6", "::1")]),
        ("visit evil.example.com now", [("domain", "evil.example.com")]),
        ("Mixed CASE Evil.Example.COM.", [("domain", "evil.example.com")]),
        ("get https://evil.example.com/a?b=1", [("url", "https://evil.example.com/a?b=1")]),
        ("(see http://x.example/path).", [("url", "http://x.example/path")]),
        ("ftp://files.example.net/drop", [("url", "ftp://files.example.net/drop")]),
        ("mail Bob.Smith@Example.org", [("email", "bob.smith@example.org")]),
        (f"md5 {MD5.upper()}", [("md5", MD5)]),
        (f"sha1={SHA1}", [("sha1", SHA1)]),
        (f"sha256:{SHA256}", [("sha256", SHA256)]),
        ("patch cve-2024-3094 now", [("cve", "CVE-2024-3094")]),
        ("CVE-2021-44228 and CVE-2014-0160", [("cve", "CVE-2021-44228"), ("cve", "CVE-2014-0160")]),
        (
            r"persistence HKLM\Software\Microsoft\Windows\CurrentVersion\Run\updater",
            [("registry_key", r"HKLM\Software\Microsoft\Windows\CurrentVersion\Run\updater")],
        ),
        (
            r"HKEY_CURRENT_USER\Software\Evil set",
            [("registry_key", r"HKEY_CURRENT_USER\Software\Evil")],
        ),
        ("dropped invoice_2026.pdf.exe", [("filename", "invoice_2026.pdf.exe")]),
        ("opened Payroll.XLSM", [("filename", "Payroll.XLSM")]),
        # Defanged forms
        ("hxxps://evil[.]example[.]com/login", [("url", "https://evil.example.com/login")]),
        ("HXXP://evil(.)example/x", [("url", "http://evil.example/x")]),
        ("fxp://drop[.]example[.]net/a", [("url", "ftp://drop.example.net/a")]),
        ("evil[dot]example[dot]com", [("domain", "evil.example.com")]),
        ("203[.]0[.]113[.]7", [("ipv4", "203.0.113.7")]),
        ("attacker[@]evil[.]example", [("email", "attacker@evil.example")]),
        ("attacker(at)evil(dot)example", [("email", "attacker@evil.example")]),
        ("hxxp[://]evil[.]example/x", [("url", "http://evil.example/x")]),
        # False positives that must not match
        ("version 2.4.1 released", []),
        ("build 10.0.19045.3803", []),
        ("octets 256.1.1.1 and 1.2.3.999", []),
        ("time 12:30:45 and mac 00:1a:2b:3c:4d:5e", []),
        ("C++ std::vector and a :: b", []),
        ("e.g. i.e. U.S.A. etc.", []),
        (
            "README.md config.yaml setup.py run.sh",
            [
                ("filename", "README.md"),
                ("filename", "config.yaml"),
                ("filename", "setup.py"),
                ("filename", "run.sh"),
            ],
        ),
        ("hex 44d88612fea8a8f36de82e1278abb02f00 is 34 chars", []),
        ("uuid 550e8400-e29b-41d4-a716-446655440000", []),
        ("CVE-21-1 is not an id", []),
        ("plain words only", []),
        # Overlapping matches: the outer observable wins
        (
            "https://203.0.113.7/payload.exe",
            [("url", "https://203.0.113.7/payload.exe")],
        ),
        ("user@mail.evil.example", [("email", "user@mail.evil.example")]),
        (
            r"HKCU\Software\evil.example.com\Run",
            [("registry_key", r"HKCU\Software\evil.example.com\Run")],
        ),
        # De-duplication keeps the first occurrence, results follow the text order
        (
            "evil.example.com 10.0.0.1 evil.example.com EVIL.example.com",
            [("domain", "evil.example.com"), ("ipv4", "10.0.0.1")],
        ),
    ],
)
def test_extract(text: str, expected: list[tuple[str, str]]) -> None:
    assert found(text) == expected


def test_extract_from_a_phishing_email() -> None:
    email = f"""From: "IT Support" <helpdesk@evil-support.example>
Received: from mail.evil-support.example (198.51.100.23)
Subject: Password expires today

Reset at hxxps://login.evil-support[.]example/reset?u=jdoe
Attachment: Reset_Instructions.docm (sha256 {SHA256})
"""
    assert found(email) == [
        ("email", "helpdesk@evil-support.example"),
        ("domain", "mail.evil-support.example"),
        ("ipv4", "198.51.100.23"),
        ("url", "https://login.evil-support.example/reset?u=jdoe"),
        ("filename", "Reset_Instructions.docm"),
        ("sha256", SHA256),
    ]


def test_extract_from_a_log_line() -> None:
    line = (
        "2026-10-31T10:00:01Z host=web-01 src=[2001:db8::5]:443 dst=192.0.2.10 "
        'proc=C:\\Windows\\Temp\\svchost.exe url="http://cdn.evil.example/a.js"'
    )
    assert found(line) == [
        ("ipv6", "2001:db8::5"),
        ("ipv4", "192.0.2.10"),
        ("filename", "svchost.exe"),
        ("url", "http://cdn.evil.example/a.js"),
    ]


@pytest.mark.parametrize(
    ("type_", "value", "expected"),
    [
        (T.IPV4, " 10.0.0.1 ", "10.0.0.1"),
        (T.IPV4, "10[.]0[.]0[.]1", "10.0.0.1"),
        (T.IPV6, "2001:DB8:0:0::1", "2001:db8::1"),
        (T.DOMAIN, "Evil.Example.com.", "evil.example.com"),
        (T.URL, "hxxp://evil[.]example/x", "http://evil.example/x"),
        (T.MD5, MD5.upper(), MD5),
        (T.EMAIL, "A@B.Example", "a@b.example"),
        (T.CVE, "cve-2024-3094", "CVE-2024-3094"),
        (T.USER, "CORP\\jdoe", "CORP\\jdoe"),
        (T.PROCESS, "powershell.exe -enc AAAA", "powershell.exe -enc AAAA"),
    ],
)
def test_normalize(type_: ObservableType, value: str, expected: str) -> None:
    assert normalize(type_, value) == expected


@pytest.mark.parametrize(
    ("type_", "value"),
    [
        (T.IPV4, "10.0.0.256"),
        (T.IPV6, "12:30:45"),
        (T.DOMAIN, "not a domain"),
        (T.URL, "evil.example/x"),
        (T.MD5, "xyz"),
        (T.SHA1, MD5),
        (T.SHA256, SHA1),
        (T.EMAIL, "nobody"),
        (T.CVE, "CVE-24-1"),
        (T.USER, "   "),
        (T.FILENAME, "two\nlines"),
    ],
)
def test_normalize_rejects(type_: ObservableType, value: str) -> None:
    with pytest.raises(ValueError, match="not a valid"):
        normalize(type_, value)


@pytest.mark.parametrize(
    ("type_", "value", "defanged"),
    [
        (T.URL, "https://evil.example.com/a.b", "hxxps://evil[.]example[.]com/a[.]b"),
        (T.URL, "ftp://drop.example/x", "fxp://drop[.]example/x"),
        (T.DOMAIN, "evil.example.com", "evil[.]example[.]com"),
        (T.IPV4, "203.0.113.7", "203[.]0[.]113[.]7"),
        (T.EMAIL, "a.b@evil.example", "a.b[@]evil[.]example"),
        (T.SHA256, SHA256, SHA256),
        (T.FILENAME, "invoice.pdf", "invoice.pdf"),
    ],
)
def test_defang_round_trips(type_: ObservableType, value: str, defanged: str) -> None:
    assert defang(type_, value) == defanged
    if type_ is not T.EMAIL:
        assert refang(defanged) == value
    assert extract(defanged) == [Candidate(type=type_, value=value)] or type_ is T.FILENAME


def test_strings_in_reads_nested_json() -> None:
    payload = {"subject": "Hi", "headers": [{"received": "from 10.0.0.1"}], "count": 3}
    assert list(strings_in(payload)) == [
        "subject",
        "Hi",
        "headers",
        "received",
        "from 10.0.0.1",
        "count",
    ]
