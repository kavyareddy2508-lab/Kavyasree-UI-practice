# =============================================================================
# IDFMCP-137 — all SES blocks for tests/infrastructure/test_foundation_constructs.py
# Imports already in the file (no change): json, pytest, aws_cdk as cdk,
# Match, Template, SesIdentityConstruct, SesIdentityProps, ParameterNames, ENV, _foundation
# =============================================================================


# -----------------------------------------------------------------------------
# BLOCK 1 — REPLACE the existing test (around lines 169-188)
# Foundation with a domain: identity, MAIL FROM, 3 DKIM outputs, SSM parameters
# -----------------------------------------------------------------------------
def test_a_configured_domain_verifies_the_domain_through_the_foundation() -> None:
    """A configured domain verifies the domain through the foundation."""
    _, template = _foundation(
        notification_sender="no-reply@idfmcp-dev.example.com",
        notification_domain="idfmcp-dev.example.com",
    )
    template.resource_count_is("AWS::SES::EmailIdentity", 1)
    template.has_resource_properties("AWS::SES::EmailIdentity", {
        "EmailIdentity": "idfmcp-dev.example.com",
        "DkimSigningAttributes": {"NextSigningKeyLength": "RSA_2048_BIT"},
        "MailFromAttributes": {
            "MailFromDomain": "mail.idfmcp-dev.example.com",
            "BehaviorOnMxFailure": "USE_DEFAULT_VALUE",
        },
    })
    template.resource_count_is("AWS::Route53::RecordSet", 0)
    outputs = template.find_outputs("*")
    assert len([key for key in outputs if "DkimRecord" in key]) == 3
    names = ParameterNames("idfmcp", "dev")
    template.has_resource_properties("AWS::SSM::Parameter", {
        "Name": names.notification_sender, "Value": "no-reply@idfmcp-dev.example.com",
    })
    template.has_resource_properties("AWS::SSM::Parameter", {
        "Name": names.notification_identity_arn, "Value": Match.any_value(),
    })


# -----------------------------------------------------------------------------
# BLOCK 2 — NO CHANGE: test_a_domain_without_a_sender_is_refused (lines 191-194)
# BLOCK 3 — NO CHANGE: _sender helper (lines 197-203)
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# BLOCK 4 — REPLACE the existing test (around lines 206-219)
# Domain identity: DKIM 2048, MAIL FROM, all 6 DNS outputs with correct values
# -----------------------------------------------------------------------------
def test_a_domain_identity_signs_with_2048_bit_dkim_and_exposes_its_records() -> None:
    """A domain identity signs with 2048 bit dkim and exposes its records."""
    sender, template = _sender(domain="idfmcp-dev.example.com")
    template.has_resource_properties("AWS::SES::EmailIdentity", {
        "EmailIdentity": "idfmcp-dev.example.com",
        "DkimAttributes": {"SigningEnabled": True},
        "DkimSigningAttributes": {"NextSigningKeyLength": "RSA_2048_BIT"},
        "ConfigurationSetAttributes": {"ConfigurationSetName": Match.any_value()},
        "MailFromAttributes": {
            "MailFromDomain": "mail.idfmcp-dev.example.com",
            "BehaviorOnMxFailure": "USE_DEFAULT_VALUE",
        },
    })
    template.resource_count_is("AWS::Route53::RecordSet", 0)
    assert len(sender.dkim_records) == 3

    outputs = template.find_outputs("*")
    dkim = [output for key, output in outputs.items() if "DkimRecord" in key]
    assert len(dkim) == 3
    assert all("CNAME" in json.dumps(output["Value"]) for output in dkim)

    values = {
        name: json.dumps(output["Value"])
        for key, output in outputs.items()
        for name in ("MailFromMxRecord", "MailFromSpfRecord", "DmarcRecord")
        if name in key
    }
    assert "MX 10 feedback-smtp.eu-central-1.amazonses.com" in values["MailFromMxRecord"]
    assert "mail.idfmcp-dev.example.com" in values["MailFromMxRecord"]
    assert "v=spf1 include:amazonses.com ~all" in values["MailFromSpfRecord"]
    assert "_dmarc.idfmcp-dev.example.com" in values["DmarcRecord"]
    assert "v=DMARC1; p=none" in values["DmarcRecord"]


# -----------------------------------------------------------------------------
# BLOCK 5 — NEW test, paste right after Block 4
# mail_from_subdomain=None: no MAIL FROM, DKIM and DMARC still output
# -----------------------------------------------------------------------------
def test_a_domain_identity_without_mail_from_outputs_only_dkim_and_dmarc() -> None:
    """A domain identity without mail from outputs only dkim and dmarc."""
    _, template = _sender(domain="idfmcp-dev.example.com", mail_from_subdomain=None)
    identity = next(iter(template.find_resources("AWS::SES::EmailIdentity").values()))
    assert "MailFromAttributes" not in identity["Properties"]
    outputs = template.find_outputs("*")
    assert not [key for key in outputs if "MailFrom" in key]
    assert len([key for key in outputs if "DkimRecord" in key]) == 3
    assert [key for key in outputs if "DmarcRecord" in key]


# -----------------------------------------------------------------------------
# BLOCK 6 — NO CHANGE: test_an_email_identity_needs_no_domain (lines 222-232)
#           (email mode still has no outputs, so find_outputs("*") == {} holds)
# BLOCK 7 — NO CHANGE: test_the_configuration_set_suppresses_bounces_and_complaints
# -----------------------------------------------------------------------------


# -----------------------------------------------------------------------------
# BLOCK 8 — REPLACE the existing parametrized test (around lines 245-256)
# Existing 5 cases kept, 3 new cases added at the end
# -----------------------------------------------------------------------------
@pytest.mark.parametrize("overrides,message", [
    ({"sender": "not-an-address"}, "not an email address"),
    ({"sender": ""}, "not an email address"),
    ({"configuration_set_name": ""}, "configuration_set_name"),
    ({"domain": ""}, "domain must not be empty"),
    ({"domain": "other.example.com"}, "is not in domain"),
    ({"domain": "idfmcp-dev.example.com", "mail_from_subdomain": "Mail_From"},
     "mail_from_subdomain is not a DNS label"),
    ({"domain": "idfmcp-dev.example.com", "mail_from_subdomain": "-mail"},
     "mail_from_subdomain is not a DNS label"),
    ({"dmarc_policy": "p=none"}, "dmarc_policy must start with v=DMARC1"),
])
def test_a_sender_ses_could_never_verify_is_refused(overrides: dict[str, object], message: str) -> None:
    """A sender ses could never verify is refused."""
    with pytest.raises(ValueError, match=message):
        _sender(**overrides)


# -----------------------------------------------------------------------------
# BLOCK 9 — NO CHANGE: test_a_service_reads_the_sender_and_identity_arn_from_the_platform
#           and the earlier tests at lines 143-166 (no SES without a sender,
#           sender published with its identity arn)
# -----------------------------------------------------------------------------
