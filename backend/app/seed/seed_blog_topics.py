#!/usr/bin/env python3
"""Seeds the initial blog content-calendar queue (BlogTopic). Idempotent by
design but not by clear-and-reload like seed_tier1_from_json.py — topics get
mutated (status: pending -> used) by the generation pipeline, so re-running
this only inserts prompts that aren't already present rather than wiping
progress."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from sqlalchemy import select

from app.db import SessionLocal  # noqa: E402
from app.models.blog import BlogTopic  # noqa: E402

TOPICS = [
    ("How credit bureaus work in Nigeria: CRC, FirstCentral and CreditRegistry explained", "credit-score",
     "credit bureau, credit score, crc, firstcentral, creditregistry, nigeria", 100),
    ("How is loan interest actually calculated? Flat rate vs reducing balance explained", "lending",
     "loan interest, flat rate, reducing balance, interest calculation", 100),
    ("Microfinance banks vs commercial banks in Nigeria: what's the difference for borrowers?", "lending",
     "microfinance, mfb, commercial bank, small business loan", 95),
    ("What is the CBN Global Standing Instruction (GSI) and how does it affect loan defaults?", "loan-recovery",
     "gsi, global standing instruction, loan default, recovery, nibss", 95),
    ("FCCPC digital lending rules: how loan apps are regulated in Nigeria", "regulation",
     "digital lending, loan app, fccpc, predatory lending, regulation", 95),
    ("SME loan readiness: what Nigerian lenders look for before approving a business loan", "sme-finance",
     "sme loan, small business, loan readiness, business finance", 90),
    ("Bank of Industry (BOI) funding: how development finance intervention funds work", "sme-finance",
     "bank of industry, boi, intervention fund, sme funding", 90),
    ("What is loan securitisation? How an SPV and tranches turn a loan book into funding", "structured-finance",
     "securitisation, spv, special purpose vehicle, loan tape, tranche", 90),
    ("Nigeria Data Protection Act and loan apps: what borrowers should know about their data", "regulation",
     "data protection, ndpa, privacy, loan app data", 85),
    ("Kenya's mobile-money credit revolution: how M-Shwari and Fuliza changed lending", "pan-africa",
     "kenya, m-pesa, mobile credit, mshwari, fuliza", 85),
    ("South Africa's National Credit Regulator: how credit is regulated outside Nigeria", "pan-africa",
     "south africa, national credit regulator, ncr, credit act", 80),
    ("Ghana's credit bureau system and what it means for cross-border lenders", "pan-africa",
     "ghana, bank of ghana, credit bureau ghana", 80),
    ("How a monthly loan repayment waterfall actually works, explained simply", "structured-finance",
     "waterfall, senior tranche, mezzanine, equity tranche, cashflow", 80),
    ("What does 'non-performing loan' (NPL) mean, and why does the ratio matter?", "lending",
     "npl, non-performing loan, npl ratio, loan quality", 75),
    ("Collateral vs cash-flow lending: two different ways African lenders assess risk", "lending",
     "collateral, cash flow lending, credit risk, underwriting", 75),
    ("How CBN's Monetary Policy Rate (MPR) filters through to loan interest rates", "regulation",
     "cbn, monetary policy rate, mpr, interest rate, central bank", 75),
    ("Digital lending in Africa: how mobile money transaction data is used for credit scoring", "pan-africa",
     "mobile money, alternative credit scoring, digital lending, africa", 70),
    ("What happens when a loan is restructured? A plain-English walkthrough", "lending",
     "loan restructuring, forbearance, repayment plan", 70),
    # --- batch 2 (Oct 2026): collections topics first — they bring schools and
    # estates to /collections/ — then more credit explainers.
    ("How schools in Nigeria can recover unpaid fees without embarrassing parents", "collections",
     "unpaid school fees, school fees recovery, bursar, parents, arrears, school", 98),
    ("Unpaid estate service charges: how residents' associations and facility managers can collect them fairly",
     "collections", "service charge, estate levy, residents association, facility manager, arrears, estate", 97),
    ("Instalment plans for overdue fees: how payment plans help people clear what they owe", "collections",
     "instalment plan, payment plan, overdue fees, arrears, school fees, service charge", 96),
    ("What a fair debt reminder looks like: tone, timing and what never to say", "collections",
     "debt reminder, polite reminder, debt collection, harassment, arrears", 94),
    ("Old arrears: why balances from past terms are still worth following up", "collections",
     "old arrears, written off, unpaid fees, recovery, school fees, service charge", 93),
    ("Collecting arrears and the Nigeria Data Protection Act: what organisations must get right", "collections",
     "ndpa, data protection, arrears, debt collection, school, estate, privacy", 92),
    ("WhatsApp reminders for unpaid bills: what works and what crosses the line", "collections",
     "whatsapp, payment reminder, unpaid bills, debt collection, arrears", 90),
    ("Success-fee debt recovery explained: how 'no recovery, no fee' collection works", "collections",
     "success fee, contingency fee, debt recovery, collection agency, arrears", 88),
    ("How to read your credit report in Nigeria and dispute an error", "credit-score",
     "credit report, credit bureau, dispute, error, crc, firstcentral, creditregistry", 87),
    ("Buy now, pay later in Nigeria: how instalment credit at checkout works", "lending",
     "buy now pay later, bnpl, instalment credit, checkout, digital lending", 86),
    ("Payday loans and salary advances: how short-term credit is priced and repaid", "lending",
     "payday loan, salary advance, short-term credit, loan app, interest", 84),
    ("Guarantors on Nigerian loans: what the role means and why lenders ask for one", "lending",
     "guarantor, loan guarantee, collateral, personal loan", 83),
    ("Cooperative societies and thrift savings: how members borrow from each other", "sme-finance",
     "cooperative society, thrift, esusu, ajo, member loans, savings", 82),
    ("Invoice financing for Nigerian SMEs: turning unpaid invoices into working capital", "sme-finance",
     "invoice financing, receivables, working capital, sme, unpaid invoices", 81),
    ("Asset financing in Nigeria: how equipment and vehicle loans work", "sme-finance",
     "asset finance, equipment loan, vehicle loan, lease, sme", 79),
    ("Debt consolidation: what it means to combine several loans into one", "lending",
     "debt consolidation, multiple loans, refinancing, repayment", 78),
    ("Loan tenor explained: how the length of a loan changes what you repay", "lending",
     "loan tenor, loan term, repayment period, total interest", 77),
    ("What lenders mean by 'debt-to-income': how affordability is assessed", "lending",
     "debt to income, affordability, underwriting, credit assessment", 76),
    ("BVN and NIN: why lenders ask for them and how identity checks work", "regulation",
     "bvn, nin, identity verification, kyc, loan application", 74),
    ("How loan apps use your phone data, and what the rules allow", "regulation",
     "loan app, phone data, contacts, privacy, fccpc, ndpa", 73),
    ("Mobile money in West Africa: how wallets are changing small loans beyond Kenya", "pan-africa",
     "mobile money, west africa, wallet, small loans, ghana, digital lending", 72),
    ("Credit scoring without a credit history: how thin-file borrowers get assessed", "credit-score",
     "thin file, alternative data, credit scoring, first-time borrower", 71),
]


def seed() -> None:
    db = SessionLocal()
    try:
        existing_prompts = set(db.scalars(select(BlogTopic.prompt)))
        added = 0
        for prompt, category, keywords, priority in TOPICS:
            if prompt in existing_prompts:
                continue
            db.add(BlogTopic(prompt=prompt, category=category, target_keywords=keywords, priority=priority))
            added += 1
        db.commit()
        print(f"seeded {added} new blog topic(s), {len(TOPICS) - added} already present")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
