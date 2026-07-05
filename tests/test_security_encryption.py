from decimal import Decimal

import pytest

from app.db.sqlite import (
    connect_database,
    get_mt5_account,
    init_db,
    list_mt5_accounts,
    save_group,
    save_mt5_account,
)
from app.domain.models import CommissionRule
from app.security.encryption import (
    ENCRYPTED_PREFIX,
    SecretCipher,
    generate_secret_key,
    is_encrypted,
)
from app.services.groups import create_group
from app.services.mt5_accounts import activate_mt5_account, create_mt5_account


def fresh_db():
    conn = connect_database()
    init_db(conn)
    return conn


def commission_rule() -> CommissionRule:
    return CommissionRule(
        partner_1_client_id="partner_1",
        partner_2_client_id="partner_2",
    )


def make_account():
    group = create_group(
        name="Gold Group",
        group_id="group_a",
        commission_rule=commission_rule(),
    )
    account = activate_mt5_account(
        create_mt5_account(
            group=group,
            account_id="account_1",
            nickname="Main Cent Account",
            broker_name="Broker A",
            server="BrokerA-Server",
            login="123456",
            sync_password="master-password",
            investor_login="123456-investor",
            investor_password="investor-password",
            display_divisor=Decimal("100"),
        )
    )
    return group, account


def test_generate_secret_key_produces_usable_cipher():
    key = generate_secret_key()
    cipher = SecretCipher(key)

    encrypted = cipher.encrypt("master-password")

    assert encrypted.startswith(ENCRYPTED_PREFIX)
    assert encrypted != "master-password"
    assert cipher.decrypt(encrypted) == "master-password"


def test_save_mt5_account_can_encrypt_password_columns():
    conn = fresh_db()
    cipher = SecretCipher(generate_secret_key())
    group, account = make_account()

    save_group(conn, group)
    save_mt5_account(conn, account, secret_cipher=cipher)

    row = conn.execute(
        "SELECT sync_password, investor_password FROM mt5_accounts WHERE account_id = ?",
        ("account_1",),
    ).fetchone()

    assert row["sync_password"].startswith(ENCRYPTED_PREFIX)
    assert row["investor_password"].startswith(ENCRYPTED_PREFIX)
    assert "master-password" not in row["sync_password"]
    assert "investor-password" not in row["investor_password"]
    assert is_encrypted(row["sync_password"]) is True


def test_encrypted_mt5_account_round_trips_with_same_cipher():
    conn = fresh_db()
    cipher = SecretCipher(generate_secret_key())
    group, account = make_account()

    save_group(conn, group)
    save_mt5_account(conn, account, secret_cipher=cipher)

    loaded = get_mt5_account(conn, "account_1", secret_cipher=cipher)
    listed = list_mt5_accounts(conn, group_id="group_a", secret_cipher=cipher)

    assert loaded == account
    assert listed == [account]
    assert loaded.sync_password.reveal() == "master-password"
    assert loaded.investor_password.reveal() == "investor-password"


def test_encrypted_mt5_account_requires_cipher_to_load():
    conn = fresh_db()
    cipher = SecretCipher(generate_secret_key())
    group, account = make_account()

    save_group(conn, group)
    save_mt5_account(conn, account, secret_cipher=cipher)

    with pytest.raises(ValueError, match="Encrypted secret requires"):
        get_mt5_account(conn, "account_1")


def test_encrypted_mt5_account_rejects_wrong_key():
    conn = fresh_db()
    cipher = SecretCipher(generate_secret_key())
    wrong_cipher = SecretCipher(generate_secret_key())
    group, account = make_account()

    save_group(conn, group)
    save_mt5_account(conn, account, secret_cipher=cipher)

    with pytest.raises(ValueError, match="Could not decrypt"):
        get_mt5_account(conn, "account_1", secret_cipher=wrong_cipher)


def test_plaintext_mt5_account_round_trip_still_works_for_old_local_test_databases():
    conn = fresh_db()
    group, account = make_account()

    save_group(conn, group)
    save_mt5_account(conn, account)

    loaded = get_mt5_account(conn, "account_1")

    assert loaded == account
