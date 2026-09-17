# PremiumBot

## Payment providers

Payment Provider Settings in the admin panel independently control FamApp, Manual Payment, and VC Gateway. FamApp and Manual Payment start enabled; VC Gateway starts disabled. VC Gateway creates a fresh ten-minute order and QR for every checkout and only activates a subscription after the stored VC order ID and amount match a successful provider response.

VC Gateway environment variables:

```env
VC_GATEWAY_API_URL=https://vcgatewaypro.com/payment_api.php
VC_GATEWAY_API_KEY=
VC_GATEWAY_UPI_ID=
```