"""High-precision labelling functions used ONLY to create training labels.

They abstain (return None) when unsure; the trained model then generalises to those docs.
The corpus is a BAILII-style scrape, so LEGISLATION / CASE_SUMMARY / GUIDANCE / TREATY are
(nearly) absent from it; SEEDS provides a few prototype headers so the model still knows them.
"""
import re

from features import MIN_TEXT

# Nominate / series law reports, recognised from the scraper's file naming.
_REPORT_FN = re.compile(
    r"^(Mor\d|Brn\d|Elchies\d|Hailes\d|\d+SLR\d|TC_\d+_\d+|Robertson_\d|"
    r"\d+_([A-Z][a-z]+|WS|SM)_\d+)")
_REPORT_TX = re.compile(
    r"scottish law reporter|cases on appeal from scotland|reports of (cases|scotch appeals)|"
    r"cases (decided )?in the house of lords")
# Old Privy Council judgments are image-only scans named <year>_<no>.pdf
_UKPC_SCAN_FN = re.compile(r"^(1[89]\d\d|20[01]\d)_\d{1,3}\.pdf$")

_OTHER_TX = re.compile(r"undertaking")
_JUDGMENT_TX = re.compile(
    r"employment tribunal|industrial tribunals|fair employment tribunal|upper tribunal|"
    r"first[- ]tier tribunal|leasehold valuation tribunal|lands tribunal|appeal tribunal|"
    r"decision notice|neutral citation|\[(19|20)\d\d\] [a-z]{2,6} |"
    r"judgment of|opinion of (the court|lord|lady)|note (by|of) sheriff|royal court|"
    r"the high court|court of appeal|supreme court|court of session|county court|"
    r"trade marks? act|dispute resolution service|decision of independent expert|"
    r"decision (and|&) reasons|determination (and reasons|promulgated)|parole board")


def weak_label(name, pages, text):
    if _REPORT_FN.match(name):
        return "LAW_REPORT"
    if pages < 0:
        return None
    if len(text) < MIN_TEXT:
        return "CASE_JUDGMENT" if _UKPC_SCAN_FN.match(name) else None
    head = text[:800].lower()
    if _REPORT_TX.search(head):
        return "LAW_REPORT"
    if "data protection act" in head[:300] and _OTHER_TX.search(head[:300]):
        return "OTHER"
    if _JUDGMENT_TX.search(head):
        return "CASE_JUDGMENT"
    return None


SEEDS = {
    "LEGISLATION": [
        "Data Protection Act 2018 2018 CHAPTER 12 An Act to make provision for the regulation of the processing of information relating to individuals. Be it enacted by the King's most Excellent Majesty, by and with the advice and consent of the Lords Spiritual and Temporal, and Commons, in this present Parliament assembled, and by the authority of the same, as follows: PART 1 Preliminary 1 Overview (1) This Act makes provision about",
        "STATUTORY INSTRUMENTS 2016 No. 1024 IMMIGRATION The Immigration (European Economic Area) Regulations 2016 Made 2nd November 2016 Laid before Parliament 3rd November 2016 Coming into force 1st February 2017 The Secretary of State, in exercise of the powers conferred by section 2(2) of the European Communities Act 1972, makes the following Regulations: Citation and commencement 1.",
        "Employment Rights Act 1996 CHAPTER 18 ARRANGEMENT OF SECTIONS PART I EMPLOYMENT PARTICULARS Right to statements of employment particulars 1 Statement of initial employment particulars. 2 Statement of initial particulars: supplementary. Schedules Schedule 1 Consequential amendments Schedule 2 Transitional provisions, savings and transitory provisions Schedule 3 Repeals and revocations",
        "SCOTTISH STATUTORY INSTRUMENTS 2019 No. 75 SHERIFF COURT Act of Sederunt (Simple Procedure Amendment) Rules 2019 Made 28th February 2019 Laid before the Scottish Parliament 4th March 2019 Coming into force 1st April 2019 Citation and commencement, etc. 1.—(1) This instrument may be cited as",
        "Number 37 of 2014 Companies Act 2014 An Act to consolidate, with amendments, certain enactments relating to companies. Be it enacted by the Oireachtas as follows: PART 1 PRELIMINARY AND GENERAL 1. Short title and commencement 2. Interpretation generally 3. Periods of time 4. Repeals and revocations",
        "A BILL TO make provision about leasehold enfranchisement and the regulation of leasehold. BE IT ENACTED by the King's most Excellent Majesty Short title, commencement and extent. In this Act section subsection paragraph Schedule the Secretary of State may by regulations made by statutory instrument",
        "REGULATION (EU) 2016/679 OF THE EUROPEAN PARLIAMENT AND OF THE COUNCIL of 27 April 2016 on the protection of natural persons with regard to the processing of personal data. THE EUROPEAN PARLIAMENT AND THE COUNCIL OF THE EUROPEAN UNION, Having regard to the Treaty on the Functioning of the European Union, Whereas: HAVE ADOPTED THIS REGULATION: CHAPTER I General provisions Article 1 Subject-matter and objectives",
        "Housing Act 1988 1988 CHAPTER 50 An Act to make further provision with respect to dwelling-houses let on tenancies. Changes to legislation: there are currently no known outstanding effects. Section 14 Determination of rent. (1) Where under subsection (4)(a) of section 13 Explanatory Notes Textual Amendments Commencement Information Extent",
    ],
    "CASE_SUMMARY": [
        "PRESS SUMMARY 15 March 2023 R (on the application of X) v Secretary of State for the Home Department [2023] UKSC 9 On appeal from [2021] EWCA Civ 100 JUSTICES: Lord Reed (President), Lord Hodge. BACKGROUND TO THE APPEAL. JUDGMENT The Supreme Court unanimously dismisses the appeal. REASONS FOR THE JUDGMENT. NOTE This summary is provided to assist in understanding the Court's decision. It does not form part of the reasons for the decision.",
        "SUMMARY OF JUDGMENT This summary is provided to assist in understanding the Court's decision. It does not form part of the reasons for the decision. The full judgment of the Court is the only authoritative document. The court today handed down judgment in the case of",
        "Judiciary of England and Wales PRESS SUMMARY Important note for press and public: this summary forms no part of the court's decision. It is provided so as to assist the press and the public to understand what the court decided. The Court of Appeal has today dismissed the appeal",
        "CASE SUMMARY Case name Citation Court Date Judges Keywords Facts The claimant brought proceedings Issue Whether Held The court held that Decision Appeal dismissed Commentary This case note summarises the key points of the decision",
        "Judgment Summary Supreme Court of the United Kingdom Background to the appeal The issue is whether Reasons for the judgment References in square brackets are to paragraphs in the judgment. This summary is provided to assist in understanding",
        "SUMMARY OF THE JUDGMENT OF THE COURT delivered by the Lord Justice Clerk. This is a summary only; the full opinion of the court with reasons will be published on the Scottish Courts and Tribunals website. The court has refused the appeal",
        "Case digest Headnote summary of the decision. Catchwords. Summary of facts. Summary of decision. The Tribunal found that. This digest is not part of the judgment and is prepared for the convenience of readers.",
        "Information note on the Court's case-law Legal summary Judgment in the case of Facts Law Article 8 Conclusion: violation (unanimously). This summary by the Registry does not bind the Court. Press release issued by the Registrar",
    ],
    "GUIDANCE_RULES_CODE": [
        "PRACTICE DIRECTION 52A APPEALS: GENERAL PROVISIONS This Practice Direction supplements CPR Part 52 Contents of this Practice Direction Section I Introduction 1.1 This Practice Direction is divided into",
        "Code of Practice on Disciplinary and Grievance Procedures Acas Foreword This Code is designed to help employers, employees and their representatives deal with disciplinary and grievance situations in the workplace. Published by TSO",
        "Presidential Guidance General Case Management This Presidential Guidance was first issued in England and Wales under the provisions of Rule 7 of the Employment Tribunals Rules of Procedure. Employment Tribunals must have regard to such guidance but they are not bound by it.",
        "Guidance Note No 2 of 2013 Joint Presidential Guidance Note: Child, vulnerable adult and sensitive appellant guidance. This guidance is issued by the Chamber President. Purpose of this guidance",
        "PRACTICE NOTE No. 1 of 2017 Commercial Actions This Practice Note has effect from 27 March 2017. It replaces Practice Note No 6 of 2004. Application and interpretation Procedure in commercial actions in the Court of Session",
        "The Tribunal Procedure (First-tier Tribunal) (Tax Chamber) Rules 2009 CONTENTS PART 1 Introduction 1. Citation, commencement, application and interpretation 2. Overriding objective and parties' obligation to co-operate with the Tribunal 3. Alternative dispute resolution PART 2 General powers and provisions 5. Case management powers",
        "Freedom of Information Act Awareness Guidance The Information Commissioner's Office has produced this guidance as part of a series of good practice guidance designed to aid understanding and application of the Act. Overview What the Act says When should the exemption be used",
        "SRA Code of Conduct for Solicitors, RELs and RFLs Introduction The Code of Conduct describes the standards of professionalism that we, the SRA, and the public expect of individuals. Maintaining trust and acting fairly 1.1 You do not unfairly discriminate Handbook Standards and Regulations",
        "Home Office Policy Guidance Immigration Rules Appendix FM Family Life Instructions for caseworkers Version 12.0 Contents About this guidance Contacts Publication Changes from last version of this guidance",
    ],
    "TREATY_INTERNATIONAL": [
        "Convention for the Protection of Human Rights and Fundamental Freedoms Rome, 4.XI.1950 The Governments signatory hereto, being members of the Council of Europe, Considering the Universal Declaration of Human Rights Have agreed as follows: Article 1 Obligation to respect Human Rights The High Contracting Parties shall secure to everyone within their jurisdiction",
        "Treaty Series No. 58 (1980) Vienna Convention on the Law of Treaties Adopted at Vienna on 22 May 1969 Presented to Parliament by the Secretary of State for Foreign and Commonwealth Affairs by Command of Her Majesty The States Parties to the present Convention",
        "CONVENTION RELATING TO THE STATUS OF REFUGEES Preamble The High Contracting Parties, Considering that the Charter of the United Nations Have agreed as follows: Chapter I General Provisions Article 1 Definition of the term refugee",
        "AGREEMENT between the Government of the United Kingdom of Great Britain and Northern Ireland and the Government of the Republic of India for the Avoidance of Double Taxation. The Contracting States Desiring to conclude Have agreed as follows Article 1 Persons covered. Done in duplicate. In witness whereof the undersigned, duly authorised",
        "United Nations General Assembly Resolution adopted by the General Assembly International Covenant on Civil and Political Rights The States Parties to the present Covenant, Considering Article 1 All peoples have the right of self-determination. Entry into force ratification accession",
        "CONSOLIDATED VERSION OF THE TREATY ON THE FUNCTIONING OF THE EUROPEAN UNION PREAMBLE HIS MAJESTY THE KING OF THE BELGIANS, DETERMINED to lay the foundations of an ever closer union HAVE DECIDED to create a European Economic Community and to this end have designated as their Plenipotentiaries",
        "Hague Convention of 25 October 1980 on the Civil Aspects of International Child Abduction The States signatory to the present Convention, Firmly convinced Have resolved to conclude a Convention to this effect, and have agreed upon the following provisions Chapter I Scope of the Convention Article 1 Contracting State",
        "Protocol to the Convention Optional Protocol The States Parties to the present Protocol Have agreed as follows Article 1 signature ratification depositary Secretary-General of the United Nations. This Protocol shall enter into force. Treaty Series Command Paper",
    ],
    "OTHER": [
        "Annual Report and Accounts 2019-20 Presented to Parliament Foreword by the Chief Executive Performance report Accountability report Financial statements Our year in numbers",
        "Dear Sir or Madam, Thank you for your letter of 3 March. I am writing to confirm that your complaint has been received. Yours faithfully, Customer Services Team",
        "Consultation paper Reforming the law Law Commission Consultation Paper No 250 Responses to this consultation should be sent by We would welcome views on the following questions Executive summary",
        "Newsletter Issue 14 Spring 2020 In this issue: News from the chambers Upcoming seminars and events Meet the team Subscribe to our mailing list Contact us",
        "Application form Please complete all sections in block capitals Section 1 Your details Surname First name Address Postcode Telephone Signature Date For office use only",
        "MONETARY PENALTY NOTICE DATA PROTECTION ACT 1998 SUPERVISORY POWERS OF THE INFORMATION COMMISSIONER To: Of: The Information Commissioner has decided to issue with a monetary penalty under section 55A of the Data Protection Act 1998",
        "Table of contents Index of cases List of abbreviations Bibliography Acknowledgements Chapter 1 Introduction This book aims to provide Lecture notes Article journal volume",
        "Cause list Daily court list Courtroom 3 Before District Judge 10:00 AM Hearing Application Time estimate 30 minutes Listing notice",
    ],
}
