import stripe
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from app.config import Settings
from app.db.users import UserStore
from pydantic import BaseModel

router = APIRouter()

class CheckoutRequest(BaseModel):
    success_url: str
    cancel_url: str

def get_billing_router(settings: Settings, user_store: UserStore):
    stripe.api_key = settings.stripe_secret_key
    
    @router.post("/checkout")
    def create_checkout_session(req: CheckoutRequest, request: Request):
        # We need the tenant_id from auth, but this is a separate router.
        # Let's assume auth logic is in HTTP or we extract token.
        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            raise HTTPException(401, "Missing token")
        
        token = auth_header.split(" ")[1]
        from app.auth import decode_access_token
        try:
            payload = decode_access_token(token)
            tenant_id = payload["tenant_id"]
            email = payload["sub"]
        except Exception:
            raise HTTPException(401, "Invalid token")
            
        billing = user_store.get_tenant_billing_status(tenant_id)
        if billing.get("subscription_status") == "active":
            raise HTTPException(400, "Already subscribed")

        try:
            # We are hardcoding a monthly price here.
            # In a real app we'd load this from config or Stripe Price ID.
            session = stripe.checkout.Session.create(
                payment_method_types=['card'],
                line_items=[{
                    'price_data': {
                        'currency': 'usd',
                        'product_data': {
                            'name': 'Corvus AI Agent - Premium Monthly',
                        },
                        'unit_amount': 4900,
                        'recurring': {
                            'interval': 'month',
                        },
                    },
                    'quantity': 1,
                }],
                mode='subscription',
                success_url=req.success_url,
                cancel_url=req.cancel_url,
                customer_email=email,
                client_reference_id=str(tenant_id),
            )
            return {"checkout_url": session.url}
        except Exception as e:
            raise HTTPException(500, str(e))

    @router.post("/webhook")
    async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
        webhook_secret = settings.stripe_webhook_secret
        if not webhook_secret:
            raise HTTPException(500, "Stripe webhook secret not configured")
            
        payload = await request.body()
        try:
            event = stripe.Webhook.construct_event(
                payload, stripe_signature, webhook_secret
            )
        except ValueError as e:
            raise HTTPException(400, "Invalid payload")
        except stripe.error.SignatureVerificationError as e:
            raise HTTPException(400, "Invalid signature")

        if event['type'] == 'checkout.session.completed':
            session = event['data']['object']
            tenant_id_str = session.get('client_reference_id')
            customer_id = session.get('customer')
            if tenant_id_str and customer_id:
                tenant_id = int(tenant_id_str)
                user_store.update_tenant_stripe_customer(tenant_id, customer_id)
                # Checkout session completed usually means active
                user_store.update_subscription_status(customer_id, 'active')
                
        elif event['type'] == 'customer.subscription.deleted':
            subscription = event['data']['object']
            customer_id = subscription.get('customer')
            if customer_id:
                user_store.update_subscription_status(customer_id, 'inactive')
                
        elif event['type'] == 'customer.subscription.updated':
            subscription = event['data']['object']
            customer_id = subscription.get('customer')
            status = subscription.get('status')
            if customer_id:
                # status can be active, past_due, canceled, unpaid
                mapped_status = 'active' if status == 'active' else 'inactive'
                user_store.update_subscription_status(customer_id, mapped_status)

        return {"status": "success"}

    return router
