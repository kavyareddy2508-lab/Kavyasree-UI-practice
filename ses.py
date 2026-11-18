"""The account's verified email sender: an SES identity, DKIM, MAIL FROM and a configuration set.

A domain identity is preferred: it is verified on a publicly resolvable domain, so any
address in it can send, messages are DKIM-signed under that domain and the custom MAIL FROM
aligns SPF for DMARC. SES verifies through public DNS only, and this construct does not write
any DNS records: every record SES needs (DKIM CNAMEs, MAIL FROM MX and SPF, DMARC) is exposed
as a stack output for the DNS owner to publish manually. Until a domain is available, an
email identity verifies the single sender address instead.

Leaving the sandbox (production access) is an account setting requested
outside of CloudFormation, per account.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_ses as ses
from constructs import Construct

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LABEL = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass
class SesIdentityProps:
    """A verified sender.

    :param sender: the from address, for example ``noreply@idfmcp-dev.vwapps.cloud.vwgroup.com``
    :param configuration_set_name: the configuration set every message is sent
        through, where bounce and complaint handling attaches
    :param domain: the public domain to verify, for example
        ``idfmcp-dev.vwapps.cloud.vwgroup.com``; the sender must be in it.
        Omit (None) to verify the sender address alone; an empty string is rejected.
    :param mail_from_subdomain: label of the custom MAIL FROM domain under ``domain``,
        for example ``mail`` gives ``mail.<domain>``; None keeps the amazonses.com default.
        Must not be a host that already has a CNAME (such as the gateway host).
    :param dmarc_policy: the DMARC TXT value to publish at ``_dmarc.<domain>``
    """

    sender: str
    configuration_set_name: str
    domain: str | None = None
    mail_from_subdomain: str | None = "mail"
    dmarc_policy: str = "v=DMARC1; p=none"


class SesIdentityConstruct(Construct):
    """An SES identity signing with DKIM, sending through its own configuration set."""

    def __init__(self, scope: Construct, construct_id: str, props: SesIdentityProps) -> None:
        """Create the configuration set and the identity, and output the DNS records to publish.

        :param scope: parent construct
        :param construct_id: unique id within the scope
        :param props: the sender configuration
        :raises ValueError: if the sender is not an address, is outside the
            domain, the configuration set has no name or the MAIL FROM label is invalid
        """
        super().__init__(scope, construct_id)
        _validate(props)

        self.sender = props.sender
        self.configuration_set = ses.ConfigurationSet(
            self, "ConfigurationSet",
            configuration_set_name=props.configuration_set_name,
            reputation_metrics=True,
            suppression_reasons=ses.SuppressionReasons.BOUNCES_AND_COMPLAINTS,
        )

        self.mail_from_domain: str | None = None
        if props.domain:
            identity = ses.Identity.domain(props.domain)
            dkim = ses.DkimIdentity.easy_dkim(ses.EasyDkimSigningKeyLength.RSA_2048_BIT)
            if props.mail_from_subdomain:
                self.mail_from_domain = f"{props.mail_from_subdomain}.{props.domain}"
        else:
            identity = ses.Identity.email(props.sender)
            dkim = ses.DkimIdentity.easy_dkim()

        self.identity = ses.EmailIdentity(
            self, "Identity",
            identity=identity,
            configuration_set=self.configuration_set,
            dkim_identity=dkim,
            dkim_signing=True,
            mail_from_domain=self.mail_from_domain,
            mail_from_behavior_on_mx_failure=(
                ses.MailFromBehaviorOnMxFailure.USE_DEFAULT_VALUE if self.mail_from_domain else None
            ),
        )
        self.identity_arn = self.identity.email_identity_arn

        self.dkim_records: list[ses.DkimRecord] = []
        if props.domain:
            self._output_dns_records(props)

    def _output_dns_records(self, props: SesIdentityProps) -> None:
        """Expose every record SES needs as an output; nothing is written to DNS by CDK.

        :param props: the sender configuration, with a domain
        """
        domain = props.domain
        self.dkim_records = list(self.identity.dkim_records)
        for index, record in enumerate(self.dkim_records, start=1):
            CfnOutput(
                self, f"DkimRecord{index}",
                description=f"DKIM CNAME {index} to publish in public DNS for {domain}",
                value=f"{record.name} CNAME {record.value}",
            )

        if self.mail_from_domain:
            region = Stack.of(self).region
            CfnOutput(
                self, "MailFromMxRecord",
                description=f"MAIL FROM MX to publish in public DNS for {domain}",
                value=f"{self.mail_from_domain} MX 10 feedback-smtp.{region}.amazonses.com",
            )
            CfnOutput(
                self, "MailFromSpfRecord",
                description=f"MAIL FROM SPF TXT to publish in public DNS for {domain}",
                value=f'{self.mail_from_domain} TXT "v=spf1 include:amazonses.com ~all"',
            )

        CfnOutput(
            self, "DmarcRecord",
            description=f"DMARC TXT to publish in public DNS for {domain}",
            value=f'_dmarc.{domain} TXT "{props.dmarc_policy}"',
        )


def _validate(props: SesIdentityProps) -> None:
    """Reject a sender SES could never verify.

    :param props: the sender configuration
    :raises ValueError: on an invalid sender, domain, configuration set name or MAIL FROM label
    """
    if not _EMAIL.match(props.sender):
        raise ValueError(f"sender is not an email address: {props.sender!r}")
    if not props.configuration_set_name:
        raise ValueError("configuration_set_name must not be empty")
    if props.domain is not None:
        if not props.domain:
            raise ValueError("domain must not be empty; omit it to verify the sender address alone")
        if not props.sender.lower().endswith(f"@{props.domain.lower()}"):
            raise ValueError(f"sender {props.sender!r} is not in domain {props.domain!r}")
        if props.mail_from_subdomain is not None and not _LABEL.match(props.mail_from_subdomain):
            raise ValueError(f"mail_from_subdomain is not a DNS label: {props.mail_from_subdomain!r}")
    if not props.dmarc_policy.startswith("v=DMARC1"):
        raise ValueError(f"dmarc_policy must start with v=DMARC1: {props.dmarc_policy!r}")



platform_app/settings.py

# field, after notification_domain
notification_mail_from: str | None = None

# load_environment, after notification_domain=
notification_mail_from=raw.get("notification_mail_from"),

# _validate, after the existing sender/domain checks
mail_from = settings.notification_mail_from
if mail_from and not domain:
    raise ValueError(f"{name}: notification_mail_from needs a notification_domain")
if mail_from and not _LABEL.fullmatch(mail_from):
    raise ValueError(f"{name}: invalid notification_mail_from {mail_from!r}")

config/environments/dev.json






idfmcp-platform README (replaces the “Platform email” bullet, lines 82–91)

- Platform email (SES, IDFMCP-137): set `notification_sender`, `notification_domain`
  and `notification_mail_from` in `config/environments/<environment>.json`. The
  foundation then creates an SES domain identity (Easy DKIM, RSA 2048) with a custom
  MAIL FROM domain and a configuration set, and publishes
  `/<prefix>/<env>/notifications/sender` and `.../identity-arn` to Parameter Store.
  CDK does not write DNS records: after deploy, copy the stack outputs
  (`DkimRecord1-3`, `MailFromMxRecord`, `MailFromSpfRecord`, `DmarcRecord`) into the
  **public** hosted zone of the domain. SES verifies over public DNS only, so the
  private (intranet) zone of the same name must not be used for these records.
  Only dev sets it (`notifications@idfmcp-dev.vwapps.cloud.vwgroup.com`, MAIL FROM
  `mail.idfmcp-dev.vwapps.cloud.vwgroup.com`); staging and prod leave it unset until
  their account is configured. SES starts in the sandbox per account and region, so
  production access has to be requested in each account (dev 544341920388 first).

idfmcp-commons README (add under the foundation constructs section)

 ### SesIdentityConstruct

An SES sender with its own configuration set (reputation metrics, account
suppression for bounces and complaints).

- With `domain`: a domain identity with Easy DKIM (RSA 2048) and a custom MAIL FROM
  domain (`mail_from_subdomain`, default `mail`). Any address in the domain can send.
- Without `domain`: an email identity for the single `sender` address (fallback only;
  no DKIM alignment under your own domain).

DNS is never written by the construct. For a domain identity it outputs every record
to publish manually in the domain's public zone:

| Output | Record |
|---|---|
| `DkimRecord1`–`3` | `<token>._domainkey.<domain>` CNAME `<token>.dkim.amazonses.com` |
| `MailFromMxRecord` | `mail.<domain>` MX `10 feedback-smtp.<region>.amazonses.com` |
| `MailFromSpfRecord` | `mail.<domain>` TXT `"v=spf1 include:amazonses.com ~all"` |
| `DmarcRecord` | `_dmarc.<domain>` TXT `"v=DMARC1; p=none"` (configurable) |

SES verifies only over public DNS; records in a private hosted zone are never found.
Production access (leaving the sandbox) is requested per account and region outside
CloudFormation.

