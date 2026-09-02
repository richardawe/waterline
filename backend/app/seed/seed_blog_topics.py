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
