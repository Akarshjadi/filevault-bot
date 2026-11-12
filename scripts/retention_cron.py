"""
Retention automation — DPDP compliance
Archives old incidents and purges audit logs.
"""
import asyncio
from datetime import datetime, timezone
from sqlalchemy import text

from database import async_session_factory
from models import File, Incident, AdminAuditLog
from storage import delete_file


async def archive_old_incidents():
    """Auto-archive incidents older than 1 year and strip names."""
    async with async_session_factory() as session:
        result = await session.execute(
            text("""
                SELECT incident_id FROM incidents
                WHERE retained_until < NOW() AND is_archived = FALSE
                LIMIT 100
            """)
        )
        old_incidents = [row[0] for row in result.fetchall()]

        for incident_id in old_incidents:
            # Strip names from persons in this incident
            await session.execute(
                text("""
                    UPDATE persons
                    SET encrypted_name = NULL, name_nonce = NULL, 
                        contact_info_encrypted = NULL, contact_nonce = NULL
                    FROM incident_persons
                    WHERE incident_persons.incident_id = :iid
                    AND persons.person_id = incident_persons.person_id
                """),
                {"iid": incident_id}
            )

            # Mark incident as archived
            await session.execute(
                text("UPDATE incidents SET is_archived = TRUE WHERE incident_id = :iid"),
                {"iid": incident_id}
            )

        await session.commit()
        print(f"[RETENTION] Archived {len(old_incidents)} incidents")


async def purge_old_audit_logs():
    """Delete audit logs older than 30 days.

    Purges both user action audit logs (audit_logs) and admin audit logs
    (admin_audit_logs) to comply with the 30-day retention policy.
    """
    async with async_session_factory() as session:
        # Purge user action audit logs
        result = await session.execute(
            text("DELETE FROM audit_logs WHERE created_at < NOW() - INTERVAL '30 days'")
        )
        user_purged = result.rowcount

        # Purge admin audit logs
        result = await session.execute(
            text("DELETE FROM admin_audit_logs WHERE created_at < NOW() - INTERVAL '30 days'")
        )
        admin_purged = result.rowcount

        await session.commit()
        total = user_purged + admin_purged
        print(f"[RETENTION] Purged {total} audit log entries ({user_purged} user, {admin_purged} admin)")


async def main():
    print(f"[RETENTION] Starting retention cron at {datetime.now(timezone.utc)}")
    await archive_old_incidents()
    await purge_old_audit_logs()
    print("[RETENTION] Cron complete")


if __name__ == "__main__":
    asyncio.run(main())
