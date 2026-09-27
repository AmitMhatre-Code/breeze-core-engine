# Before you begin

Breeze Modern talks to ICICI through the **Breeze API**. Before you can register, you need an API app on ICICI's side that knows where your copy of Breeze Modern lives. This takes about ten minutes and you only do it once.

## What you need

| You need | Why |
|---|---|
| An **ICICI Direct trading account** with F&O enabled | Breeze Modern trades through your own account. Your ICICI user id becomes your Breeze Modern username. |
| Your **server's static IP address** | ICICI only accepts API calls from IP addresses registered against your API app. Your server has a fixed (static) public IP, shown when your deployment was set up at [breeze-ui.com](https://breeze-ui.com). It is also the address you type in your browser to open Breeze Modern. |
| A **Breeze API app** registered on ICICI's API portal | This gives you the **API Key** and **Secret Key** you enter when you register. |
| A modern browser | Chrome, Edge, Safari or Firefox, on a desktop or laptop. Phones work, but dense tables are easier on a bigger screen. |

## Register your API app with ICICI

The easiest way is to follow the step-by-step guide linked from the Breeze Modern login page (**Need help with registration of Static IP? Read instructions here**). It opens on breeze-ui.com with your server's IP address and redirect URL already filled in, so you can copy them exactly.

In outline, the steps are:

1. **Sign in to the ICICI Breeze API portal** at [api.icicidirect.com](https://api.icicidirect.com/apiuser/home) with your ICICI Direct credentials.
2. Open **Register an App**.
3. Fill in the form:
   - **App Name**: anything you like.
   - **Redirect URL**: your server's address followed by `/icici-return`, for example `http://203.0.113.10/icici-return`. After you log in to ICICI, this is where ICICI sends you back to Breeze Modern. It must match exactly.
   - **Primary IP Address**: your server's static IP, for example `203.0.113.10`.
   - **Secondary IP Address**: leave blank.
4. Click **Submit**.
5. Open **View Apps** and note the **API Key** and **Secret Key** for your new app.

> [!IMPORTANT]
> Treat the Secret Key like a password. Breeze Modern never asks you to type the whole of it in one place. When you register, you enter most of it; the last few characters you memorise and type at each sign-in. [Registering your account](registering.md) explains how to split it.

## If your IP address or redirect URL is wrong

ICICI rejects the sign-in hand-off or the API calls, and Breeze Modern shows an ICICI error instead of taking you to the Dashboard. The usual causes are:

- The **Redirect URL** has a typo, uses `https` where your server uses `http`, or ends with a slash.
- The **Primary IP Address** is not your server's current static IP (for example after a server was recreated with a new address).

Correct the app on ICICI's portal, then sign in again. You do not need to re-register in Breeze Modern unless ICICI gave you a new API Key or Secret Key; if it did, see [Passwords and credentials](account-recovery.md#your-icici-api-key-or-secret-changed).

## Next step

With your API Key and Secret Key in hand, [register your account](registering.md).
