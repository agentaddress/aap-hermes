def test_package_imports():
    import aap_hermes
    assert aap_hermes.__version__  # any non-empty string; avoids pin churn on bumps


def test_aap_is_importable_from_vendored_wheel():
    """The vendored aap wheel must be installed and importable."""
    import aap
    assert hasattr(aap, "Envelope")
    assert hasattr(aap, "generate_keypair")


def test_aap_v06_primitives_importable():
    """v0.6.0: capability machinery is gone; services + relationships replace it."""
    from aap import (
        ServiceRequest,
        ServiceResponse,
        ServiceResponseStatus,
        RelationshipProposal,
        RelationshipAccept,
        RelationshipDecline,
        RelationshipRevoke,
        ServiceFollowupGrant,
        ServiceFollowup,
    )
    assert ServiceRequest.PAYLOAD_TYPE == "aap.service-request/v1"
    assert ServiceResponse.PAYLOAD_TYPE == "aap.service-response/v1"
    assert ServiceResponseStatus.CONFIRMED.value == "confirmed"
    assert RelationshipProposal.PAYLOAD_TYPE == "aap.relationship-proposal/v1"
    assert RelationshipAccept.PAYLOAD_TYPE == "aap.relationship-accept/v1"
    assert RelationshipDecline.PAYLOAD_TYPE == "aap.relationship-decline/v1"
    assert RelationshipRevoke.PAYLOAD_TYPE == "aap.relationship-revoke/v1"
    assert ServiceFollowupGrant.PAYLOAD_TYPE == "aap.service-followup-grant/v1"
    assert ServiceFollowup.PAYLOAD_TYPE == "aap.service-followup/v1"


def test_capability_machinery_removed():
    """Old capability/scope/token vocabulary must NOT be importable from aap
    anymore — its presence here would mean an incomplete cutover."""
    import aap
    for removed in (
        "CapabilityRequest",
        "CapabilityGrant",
        "CapabilityDenial",
        "CapabilityRefresh",
        "RelationshipToken",
        "AccessDenied",
        "CapabilityCatalog",
    ):
        assert not hasattr(aap, removed), f"aap.{removed} still exists after cutover"


def test_aap_v04_primitives_importable():
    """Group primitives remain — orthogonal to the v0.6 cutover."""
    from aap import GroupInvitation, GroupMembershipUpdate, GroupLeave
    assert GroupInvitation.PAYLOAD_TYPE == "aap.group-invitation/v1"
    assert GroupMembershipUpdate.PAYLOAD_TYPE == "aap.group-membership-update/v1"
    assert GroupLeave.PAYLOAD_TYPE == "aap.group-leave/v1"


def test_aap_v05_primitives_importable():
    """Verification + discovery primitives remain — orthogonal to the v0.6 cutover."""
    from aap import (
        VerificationAttestation,
        DiscoveryIntroductionRequest,
        DiscoveryIntroductionResponse,
        VerifierTrustListEntry,
        parse_trusted_verifiers,
    )
    assert VerificationAttestation.PAYLOAD_TYPE == "aap.verification-attestation/v1"
    assert (
        DiscoveryIntroductionRequest.PAYLOAD_TYPE
        == "aap.discovery-introduction-request/v1"
    )
    assert (
        DiscoveryIntroductionResponse.PAYLOAD_TYPE
        == "aap.discovery-introduction-response/v1"
    )
    assert VerifierTrustListEntry is not None
    assert callable(parse_trusted_verifiers)
