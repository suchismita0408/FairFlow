"""Prepare a Sandbox freelancer connection for an existing local project.

This creates an onboarding link, never a charge or signature. The freelancer
must still grant permissions in PayPal. Credentials and tokens are not logged.
"""
import argparse
import sys
import asyncio
from pathlib import Path
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('project_id')
    args=parser.parse_args()
    load_dotenv(ROOT/'.env')
    import main as runtime
    from app import db,platform_paypal as pp,funded_payments as funded
    with db.engine().connect() as conn:
        member=db.one(conn,db.members,(db.members.c.project_id==args.project_id)&(db.members.c.role=='freelancer'))
        if not member:raise SystemExit('Project freelancer not found.')
        user=db.one(conn,db.users,db.users.c.id==member['user_id'])
    email=pp.config().get('PAYPAL_PLATFORM_SELLER_EMAIL')
    if not email:raise SystemExit('Set PAYPAL_PLATFORM_SELLER_EMAIL in the private platform configuration.')
    def routes(items):
        for route in items:
            yield route
            if hasattr(route,'original_router'):yield from routes(route.original_router.routes)
    endpoint=next(route.endpoint for route in routes(runtime.app.routes) if getattr(route,'path','')=='/api/{portal}/projects/{pid}/assurance/platform/connect')
    result=asyncio.run(endpoint(portal='freelancer',pid=args.project_id,data=funded.SellerInput(email=email),user=user))
    # The link stays in the authenticated freelancer portal instead of logs.
    print('Project receiving-account connection prepared for '+email)
    print('Open freelancer Payments and select Continue in PayPal.')
    print('No payment or agreement acceptance was created.')


if __name__=='__main__':main()
