"""ICICI customer details identity parsing."""

from icici_breeze_backend.app.services.icici_customer_identity import (
    parse_customer_details_identity,
)


def test_parse_prefers_api_id_and_name():
    customer = {
        "Status": 200,
        "Success": {"id": "vikrammh", "idirect_user_name": "VIKRAM M HATRE"},
    }
    uid, name = parse_customer_details_identity(customer, fallback_user_id="OTHER")
    assert uid == "VIKRAMMH"
    assert name == "VIKRAM M HATRE"


def test_parse_falls_back_to_form_user_id():
    uid, name = parse_customer_details_identity(None, fallback_user_id="formuser")
    assert uid == "FORMUSER"
    assert name is None
