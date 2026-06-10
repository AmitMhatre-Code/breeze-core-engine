# TERMS AND CONDITIONS FOR BREEZE MODERN

**Last Updated:** June 10, 2026  
**Effective Date:** June 10, 2026

PLEASE READ THESE TERMS AND CONDITIONS ("TERMS", "AGREEMENT") CAREFULLY BEFORE ACTIVATING A LICENSE KEY OR DEPLOYING THE SOFTWARE. THIS AGREEMENT CONSTITUTES A LEGALLY BINDING CONTRACT BETWEEN YOU (THE "USER", "LICENSEE") AND THE PROVIDER OF THE BREEZE ECOSYSTEM ("COMPANY", "WE", "OUR", "US").

BY CREATING A LICENSE, INITIATING AN AWS CLOUDFORMATION STACK DEPLOYMENT, AUTHENTICATING VIA GOOGLE OAUTH, OR UTILIZING THE BREEZE CONSOLE, YOU EXPLICITLY ACKNOWLEDGE THAT YOU HAVE READ, UNDERSTOOD, AND AGREE TO BE BOUND BY ALL PROVISIONS CONTAINED HEREIN. IF YOU DO NOT AGREE, YOU MUST IMMEDIATELY CEASE ACCESSING THE PORTAL AND DESTROY ALL BREEZE MODERN ARTIFACTS.

---

## 1. NATURE OF SERVICE AND ARCHITECTURAL BOUNDARY

### 1.1 Private Cloud Deployment Model
The software application known as **Breeze Modern** is distributed as an independent containerized application package. When initialized via the **Breeze Console** web platform (hosted at `https://breeze-ui.com`), it is deployed through automated infrastructure-as-code scripts (such as AWS CloudFormation templates) directly into the User’s private, self-funded, and self-managed Amazon Web Services ("AWS") cloud infrastructure account.

### 1.2 Technical Separation of Boundaries
The User explicitly acknowledges that the Company acts strictly as a software vendor and infrastructure-automation provider. The Company does not host, operate, control, or maintain the active runtime execution loops of **Breeze Modern** once it is established inside the User’s private AWS environment. The physical infrastructure, network layers, operating systems, and firewalls are entirely within the care, custody, and control of the User.

### 1.3 Self-Managed Infrastructure Responsibility
The User assumes sole and exclusive responsibility for all maintenance, data persistence management, cost management, and security configurations relating to their AWS account. This includes, but is not limited to, EC2 compute charges, storage costs (S3 bucket fees, Write-Ahead Log storage optimization, EBS volumes), network bandwidth, and resource utilization alerts.

---

## 2. STRICT FINANCIAL AND ALGORITHMIC TRADING DISCLAIMERS

### 2.1 No Investment or Financial Advice
The Company is a technical infrastructure and software provider. We do not provide financial advice, investment advisory services, portfolio management, or trade recommendation logic. No content, documentation, strategy configurations, or telemetry metrics displayed on the **Breeze Console** shall be construed as an endorsement, financial recommendation, or solicitation to buy or sell securities, options, or any other financial instruments.

### 2.2 Inherent Risks of Automated Algorithmic Trading
Algorithmic, options-based, and automated trading involve extreme technical and financial hazards. The User explicitly acknowledges and accepts all risks associated with:
* **Execution Slippage:** Differences between theoretical model entry prices and actual broker fills.
* **Network Latency:** Telemetry lags, API response delays, or transmission drops between the User's EC2 node and external broker gateways.
* **Broker API Rate-Limiting:** Sudden or systematic transaction rejections, token expirations, or connectivity throttling imposed by external brokerage firms (such as ICICI Direct).
* **Logic Loop & Concurrency Cascades:** Software operational risks where mechanical execution conditions trigger unintended, compounding, or rapid sequential trade executions.

### 2.3 "As-Is" Software Provision and Asset Risk
**Breeze Modern** and the **Breeze Console** are provided on an **"AS IS"** and **"AS AVAILABLE"** basis, with all faults and without warranties of any kind, express or implied. The User trades entirely at their own risk and using their own capital. The Company assumes zero responsibility or liability for capital losses, financial drawdowns, margin calls, or unintended market exposure resulting from code defects, system rollbacks, system upgrades, thread blockages, or technical anomalies.

---

## 3. DATA PRIVACY, ZERO-CUSTODY, AND ENCRYPTED ARCHITECTURE

### 3.1 Zero-Custody Policy
The Company enforces a strict **Zero-Custody Architecture** regarding the sensitive operational parameters of the User. The Company's centralized servers and **Breeze Console** infrastructure do not ingest, record, transmit, view, or retain:
* The User's specific brokerage API authentication keys, session tokens, or private login secrets.
* The User's algorithmic trading strategies, underlying indicator formulas, or asset distribution models.
* The User's absolute historical trading records or complete transaction ledger parameters.

All such configurations are executed, encrypted, and processed entirely inside the localized storage and random-access memory (RAM) bounds of the User’s private AWS instance node running **Breeze Modern**.

### 3.2 Telemetry and Heartbeat Reporting
To ensure license validation, Digital Rights Management (DRM) compliance, and basic operational health status modeling, **Breeze Modern** automatically transmits a low-footprint cryptographic payload back to the **Breeze Console** (the "Heartbeat Pipeline"). This payload contains only non-custodial metadata, specifically:
* The system deployment identifier and active License Key hash.
* The public IP address assigned to the host instance.
* A basic boolean health status indicator (`Healthy`, `Pending`, `Action Required`).

### 3.3 Public Third-Party Integrations
The **Breeze Console** utilizes Google OAuth for unified user login processing. Authentication workflows are subject to the respective privacy and security rules of Google LLC. The Company is not responsible for credential breaches occurring at the provider level.

---

## 4. COMMERCIAL TERMS, LICENSING, AND ENFORCEMENT

### 4.1 License Grant and Bounds
Subject to the timely payment of applicable license fees, the Company grants the User a limited, non-exclusive, non-transferable, revocable, and non-sublicensable license to install and run the compiled containerized binaries of **Breeze Modern** on a single concurrent virtual computing machine container per issued license key.

### 4.2 Prohibited Exploitations and Reverse Engineering
The User shall not, and shall not permit any third party to:
* Decompile, disassemble, reverse-engineer, decapsulate, or attempt to extract the underlying source code of **Breeze Modern** or **Breeze Console** wrappers.
* Intercept, spoof, alter, or bypass the cryptographic JWT tokens or policy payloads transmitted via the Heartbeat Pipeline.
* Modify the underlying Digital Rights Management (DRM) parameters or public key verification schemas (`/etc/breeze/portal_heartbeat_public.pem`).
* Multiplex, redistribute, or scale container runtimes to run multiple core engine instances against a single unauthorized license key allocation.

Any attempt to execute any item in this section results in the immediate, automated, and permanent cancellation of all associated licenses without notice or right to cure.

### 4.3 Subscription, Renewals, and Cancellations
All licensing fees are billed on a recurring subscription basis (monthly or annually) as configured during the registration checkout process. Subscriptions automatically renew using the authorized payment method on file unless canceled by the User prior to the billing cycle expiration date via the **Breeze Console**.

### 4.4 Absolute No-Refund Policy
Due to the instant accessibility of the downloadable software artifacts, infrastructure scripts, and immediate cryptographic token provisioning, **ALL PAYMENTS ARE FINAL AND COMPLETELY NON-REFUNDABLE.** The Company does not offer pro-rated refunds for partial subscription months or unused license durations following a voluntary cancellation request.

---

## 5. LIFECYCLE MANAGEMENT, AUTOMATED UPGRADES, AND ROLLBACKS

### 5.1 Asynchronous Update Rights
To optimize trading performance, maintain broker compatibility, and address technical edge-case security risks, the Company reserves the absolute right to publish updated core versions of **Breeze Modern** and manipulate target production version pointers.

### 5.2 Mandatory Automated Update Acceptance
The User explicitly acknowledges that their localized environment running **Breeze Modern** may be configured to dynamically pull or transition to updated images or execute automated rollbacks to historical baselines based on the cryptographic policy tokens pushed down via the heartbeat handshake loop. The Company carries no liability for any temporary trading interruption, instance reboot latency, or session logout occurring during these automated lifecycle events.

---

## 6. INTELLECTUAL PROPERTY RIGHTS

The User explicitly acknowledges that all right, title, and interest in and to **Breeze Modern**, the **Breeze Console** dashboard layout components, the CloudFormation automation templates, the proprietary DRM key verification frameworks, and all associated source files, designs, trademarks, logos, and documentation are—and shall remain—the exclusive, unencumbered Intellectual Property of the Company and its licensors. No ownership transfer is implied or executed under this Agreement.

---

## 7. LIMITATION OF LIABILITY AND INDEMNIFICATION

### 7.1 Complete Waiver of Consequential Damages
TO THE MAXIMUM EXTENT PERMITTED BY APPLICABLE LAW, IN NO EVENT SHALL THE COMPANY, ITS DIRECTORS, EMPLOYEES, AFFILIATES, OR SUPPLIERS BE LIABLE FOR ANY INDIRECT, PUNITIVE, INCIDENTAL, SPECIAL, CONSEQUENTIAL, OR EXEMPLARY DAMAGES, INCLUDING WITHOUT LIMITATION DAMAGES FOR LOSS OF PROFITS, LOSS OF CAPITAL, TRADING LOSSES, LOSS OF GOODWILL, LOSS OF DATA, OR OTHER INTANGIBLE LOSSES, ARISING OUT OF OR RELATING TO THE USE OF, OR INABILITY TO USE, THIS SOFTWARE.

### 7.2 Liability Cap
UNDER NO CIRCUMSTANCES SHALL THE COMPANY’S TOTAL AGGREGATE LIABILITY FOR ALL CLAIMS, DISPUTES, OR ACTIONS EXCEED THE EXACT AMOUNT PAID BY THE SPECIFIC USER TO THE COMPANY FOR THE SINGLE ACTIVE LICENSE KEY IN QUESTION DURING THE IMMEDIATE THREE (3) MONTH PERIOD PRECEDING THE EVENT GIVING RISE TO LIABILITY.

### 7.3 Indemnification
The User agrees to defend, indemnify, and hold harmless the Company and its agents from and against any and all claims, damages, obligations, losses, liabilities, costs, debts, and expenses (including attorney's fees) arising from the User's misuse of **Breeze Modern**, violation of brokerage rules, infrastructure misconfigurations, or breach of any provision of these Terms.

---

## 8. GOVERNING LAW AND DISPUTE RESOLUTION

### 8.1 Governing Jurisdiction
This Agreement shall be governed by, construed, and enforced exclusively in accordance with the internal substantive laws of the country, state, or regional jurisdiction where the Company is officially incorporated, without giving effect to any principles of conflicts of law.

### 8.2 Mandatory Arbitration
Any dispute, controversy, or claim arising out of or relating to this contract, including its formation or breach, shall be settled by binding arbitration in accordance with the standard commercial arbitration rules of the designated local governing body. The place of arbitration shall be the official corporate city of the Company, the language shall be English, and judgment on the award rendered by the arbitrator(s) may be entered in any court having competent jurisdiction.

---

## 9. AMENDMENTS AND SYSTEM NOTICES

The Company reserves the right, at its sole discretion, to modify, update, or replace these Terms at any time by posting the revised text on the **Breeze Console** user panel. It is the User's responsibility to periodically review these Terms for changes. Continued use of **Breeze Modern** or the **Breeze Console** following the posting of modifications constitutes explicit acceptance of the newly revised Terms and Conditions.

---

## 10. CONTACT AND ACKNOWLEDGMENT

By activating your license key inside the **Breeze Console**, you solemnly verify that you are at least 18 years of age, possess the legal capacity to enter into a binding financial infrastructure arrangement, are a qualified or authorized retail trader within your resident jurisdiction, and agree completely to these Terms and Conditions.

For technical support inquiries or infrastructure compliance documentation, please contact the administrative support network via the official channels designated inside the **Breeze Console**.