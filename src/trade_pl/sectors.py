from __future__ import annotations

# Curated liquid US names by GICS-style sector (aim ~500–1000 when combined).
# Used for offline universe building; yfinance sector used as fallback when enriching.

SECTOR_TICKERS: dict[str, list[str]] = {
    "Technology": [
        "AAPL", "MSFT", "NVDA", "AVGO", "ORCL", "CRM", "AMD", "ADBE", "CSCO", "ACN",
        "IBM", "INTC", "TXN", "QCOM", "AMAT", "NOW", "PANW", "MU", "ADI", "KLAC",
        "CDNS", "SNPS", "ANET", "APH", "ADSK", "FTNT", "CRWD", "PLTR", "SNOW", "INTU",
        "SHOP", "TEAM", "DDOG", "NET", "ZS", "MDB", "WDAY", "VEEV", "FTV", "KEYS",
        "MPWR", "ON", "SWKS", "MCHP", "LRCX", "TER", "ENTG", "SMCI", "ARM", "APP",
        "DELL", "HPQ", "HPE", "NTAP", "STX", "WDC", "GEN", "FFIV", "AKAM", "VRSN",
        "CTSH", "IT", "EPAM", "GLOB", "PATH", "U", "CFLT", "BILL", "OKTA", "TWLO",
        "ZI", "HUBS", "DOCU", "PCTY", "PAYC", "TYL", "MANH", "FICO", "ANSS", "PTC",
        "SSNC", "GDDY", "JKHY", "CDW", "FLEX", "JBL", "GLW", "TEL", "TRMB", "ZBRA",
    ],
    "Communication Services": [
        "GOOGL", "GOOG", "META", "NFLX", "DIS", "CMCSA", "T", "VZ", "TMUS", "CHTR",
        "EA", "TTWO", "RBLX", "SPOT", "LYV", "WBD", "PARA", "FOX", "FOXA", "NWSA",
        "NWS", "MTCH", "PINS", "SNAP", "RDDT", "TTD", "OMC", "IPG", "NYT", "ROKU",
    ],
    "Consumer Cyclical": [
        "AMZN", "TSLA", "HD", "MCD", "LOW", "TJX", "BKNG", "SBUX", "NKE", "CMG",
        "ORLY", "MAR", "GM", "F", "TGT", "AZO", "ROST", "DHI", "LEN", "PHM",
        "NVR", "TOL", "KBH", "YUM", "DPZ", "QSR", "DRI", "TXRH", "ABNB", "UBER",
        "LYFT", "EBAY", "ETSY", "W", "RH", "DECK", "LULU", "ULTA", "BBY", "GPC",
        "AAP", "POOL", "TSCO", "DKS", "BURL", "FIVE", "WSM", "PZZA", "CAVA", "SHAK",
        "HLT", "H", "WH", "CHH", "EXPE", "CCL", "RCL", "NCLH", "WYNN", "LVS",
        "MGM", "CZR", "PENN", "DKNG", "CVNA", "AN", "LAD", "PAG", "KMX", "GPI",
    ],
    "Consumer Defensive": [
        "WMT", "COST", "PG", "KO", "PEP", "PM", "MO", "MDLZ", "CL", "KMB",
        "GIS", "K", "HSY", "SJM", "CPB", "CAG", "TSN", "HRL", "MKC", "CHD",
        "CLX", "KR", "SYY", "ADM", "BG", "CASY", "SFM", "DG", "DLTR", "BJ",
        "ELF", "COTY", "CELH", "MNST", "KDP", "STZ", "BF-B", "TAP", "SAM", "USFD",
    ],
    "Energy": [
        "XOM", "CVX", "COP", "SLB", "EOG", "MPC", "PSX", "VLO", "WMB", "OKE",
        "KMI", "HES", "FANG", "DVN", "HAL", "BKR", "TRGP", "LNG", "EQT", "CTRA",
        "MRO", "APA", "OXY", "PR", "CHRD", "MTDR", "RRC", "AR", "CNX", "SWN",
        "HP", "NOV", "FTI", "WHD", "LBRT", "PTEN", "RIG", "VAL", "TDW", "NBR",
        "ENPH", "FSLR", "SEDG", "RUN", "PLUG", "BE", "CWEN", "NEP", "AES", "VST",
    ],
    "Financials": [
        "BRK-B", "JPM", "V", "MA", "BAC", "WFC", "MS", "GS", "BX", "SPGI",
        "AXP", "BLK", "C", "SCHW", "CB", "MMC", "PGR", "FI", "ICE", "CME",
        "AON", "USB", "PNC", "TFC", "COF", "AIG", "MET", "PRU", "AFL", "ALL",
        "TRV", "AJG", "BRO", "WRB", "CINF", "L", "EG", "RNR", "ACGL", "HIG",
        "MTB", "FITB", "HBAN", "RF", "CFG", "KEY", "NTRS", "STT", "BK", "TROW",
        "BEN", "IVZ", "AMP", "RJ", "LPLA", "SF", "HOOD", "SOFI", "SYF", "DFS",
        "ALLY", "COIN", "IBKR", "NDAQ", "CBOE", "MSCI", "MCO", "FDS", "VRSK", "PAYX",
        "ADP", "FIS", "GPN", "JKHY", "TOST", "AFRM", "UPST", "LC", "XYZ", "PYPL",
    ],
    "Healthcare": [
        "LLY", "UNH", "JNJ", "ABBV", "MRK", "TMO", "ABT", "DHR", "PFE", "AMGN",
        "ISRG", "SYK", "VRTX", "BSX", "CI", "GILD", "REGN", "MDT", "BMY", "ZTS",
        "BDX", "HCA", "ELV", "CVS", "MCK", "COR", "CAH", "CNC", "HUM", "MOH",
        "DGX", "LH", "IQV", "A", "WAT", "MTD", "IDXX", "DXCM", "PODD", "HOLX",
        "RMD", "STE", "ZBH", "ALGN", "BAX", "TFX", "BIO", "TECH", "CRL", "MEDP",
        "BIIB", "MRNA", "SGEN", "ALNY", "BMRN", "INCY", "EXAS", "GH", "NTRA", "ILMN",
        "NVCR", "NBIX", "UTHR", "VRTX", "SRPT", "RARE", "PCVX", "VKTX", "BBIO", "CYTK",
        "JAZZ", "HALO", "ARGX", "BGNE", "LEGN", "RVMD", "ROIV", "MDGL", "AXSM", "ITCI",
    ],
    "Industrials": [
        "CAT", "GE", "HON", "UNP", "RTX", "DE", "LMT", "ETN", "ADP", "WM",
        "ITW", "PH", "EMR", "NOC", "CSX", "TDG", "CTAS", "GD", "MMM", "FDX",
        "UPS", "NSC", "PCAR", "URI", "CARR", "OTIS", "JCI", "ROK", "DOV", "IR",
        "SWK", "SNA", "GWW", "FAST", "PAYX", "VRSK", "EFX", "CPRT", "ODFL", "XPO",
        "SAIA", "CHRW", "EXPD", "JBHT", "LSTR", "KNX", "DAL", "UAL", "AAL", "LUV",
        "ALK", "BA", "TXT", "HWM", "HEI", "TDY", "LHX", "AXON", "CW", "WWD",
        "GNRC", "PWR", "EME", "FIX", "ACM", "J", "FLR", "MTZ", "DY", "PRIM",
        "BLDR", "MAS", "OC", "AOS", "TREX", "FBIN", "ALLE", "TT", "XYL", "PNR",
    ],
    "Utilities": [
        "NEE", "SO", "DUK", "CEG", "SRE", "AEP", "D", "EXC", "PEG", "XEL",
        "ED", "WEC", "ES", "AWK", "DTE", "EIX", "PPL", "FE", "AEE", "CMS",
        "CNP", "ATO", "LNT", "EVRG", "NI", "PNW", "IDA", "OGE", "NFG", "SWX",
        "NRG", "VST", "TLN", "AES", "PCG", "ETR", "WTRG", "MSEX", "YORW", "CWEN",
    ],
    "Real Estate": [
        "PLD", "EQIX", "AMT", "CCI", "SPG", "O", "PSA", "WELL", "DLR", "EXR",
        "VICI", "AVB", "EQR", "INVH", "MAA", "UDR", "ESS", "CPT", "ARE", "BXP",
        "VTR", "PEAK", "DOC", "HR", "OHI", "SBRA", "CTRE", "MPW", "IRM", "WY",
        "SBAC", "LAMR", "OUT", "GLPI", "REG", "FRT", "KIM", "BRX", "NNN", "ADC",
        "STAG", "EGP", "REXR", "TRNO", "FR", "IIPR", "CUBE", "LSI", "NSA", "SELF",
    ],
    "Materials": [
        "LIN", "SHW", "APD", "ECL", "FCX", "NEM", "CTVA", "NUE", "DOW", "DD",
        "PPG", "VMC", "MLM", "FAST", "ALB", "FMC", "CF", "MOS", "IFF", "LYB",
        "EMN", "CE", "WLK", "RPM", "AXTA", "ASH", "OLN", "HUN", "SEE", "SON",
        "PKG", "IP", "WRK", "GPK", "SLGN", "AMCR", "BALL", "CCK", "ATR", "AVY",
        "STLD", "RS", "CMC", "CLF", "X", "AA", "SCCO", "TECK", "RIO", "BHP",
    ],
    "Basic Materials / Mining ETFs peers": [
        "GOLD", "AEM", "WPM", "FNV", "RGLD", "AGI", "PAAS", "HL", "CDE", "EGO",
    ],
    # Additional liquid mid/large names to push toward ~1000 ranked names
    "Technology Extra": [
        "GFS", "ASTS", "RGTI", "IONQ", "QBTS", "SOUN", "AI", "BBAI", "UPST", "AFRM",
        "TOST", "FOUR", "SQ", "SHOP", "MELI", "SE", "GRAB", "CPNG", "NU", "XP",
        "TSM", "ASML", "SAP", "SONY", "NTES", "BIDU", "JD", "PDD", "BABA", "TME",
        "LOGI", "STM", "NXPI", "MRVL", "LSCC", "QRVO", "CRUS", "ALGM", "WOLF", "SLAB",
        "FORM", "ACLS", "AMKR", "COHR", "LSR", "VIAV", "FN", "SANM", "PLXS", "TTMI",
        "CIEN", "JNPR", "EXTR", "CALX", "COMM", "VSAT", "GSAT", "IRDM", "SATS", "LITE",
        "SMAR", "ESTC", "DT", "NEWR", "SPLK", "NOW", "CRM", "WDAY", "SNOW", "DDOG",
        "GTLB", "S", "RPD", "TENB", "QLYS", "VRNS", "SAIL", "PING", "FROG", "MNDY",
        "BRZE", "FRSH", "ASAN", "CDAY", "PAY", "INST", "ALRM", "APPF", "BL", "QTWO",
    ],
    "Healthcare Extra": [
        "TMO", "DHR", "A", "WAT", "MTD", "TECH", "BIO", "CRL", "IQV", "ICLR",
        "RGEN", "BRKR", "CTLT", "LNTH", "INSM", "BPMC", "KRYS", "RYTM", "CRNX", "ACAD",
        "SUPN", "AMRX", "PCRX", "COLL", "HRMY", "LGND", "DVAX", "MRTX", "KROS", "RCKT",
        "FOLD", "AGIO", "BLUE", "BEAM", "CRSP", "NTLA", "EDIT", "VERV", "PRME", "RXRX",
        "SDGR", "CERT", "DOCS", "TDOC", "OSCR", "ALHC", "CLOV", "ACC", "GH", "TWST",
        "TXG", "BLI", "PACB", "OMIC", "DNA", "ABCL", "SDGR", "VCYT", "FLGT", "CDNA",
        "TNDM", "PODD", "DXCM", "ABT", "BSX", "SYK", "ZBH", "EW", "ISRG", "GMED",
        "CNMD", "ITGR", "LIVN", "NVST", "XRAY", "HSIC", "PDCO", "OMI", "MMSI", "HAE",
    ],
    "Energy Extra": [
        "SHEL", "BP", "TTE", "PBR", "EQNR", "E", "SU", "CNQ", "CVE", "IMO",
        "ENB", "TRP", "PBA", "WDS", "BTE", "ERF", "OVV", "CHK", "RRC", "SWN",
        "GPOR", "CRK", "SM", "MTDR", "NOG", "CIVI", "PR", "VTLE", "MUR", "CRC",
        "DEN", "TAL", "BTU", "ARCH", "AMR", "HCC", "CEIX", "ARLP", "NC", "NRP",
        "OII", "RES", "WTTR", "PUMP", "WHD", "AESI", "XPRO", "SLCA", "CLB", "DRQ",
    ],
    "Financials Extra": [
        "HSBC", "RY", "TD", "BNS", "BMO", "CM", "MUFG", "SMFG", "NMR", "UBS",
        "DB", "CS", "ING", "SAN", "BBVA", "BCS", "LYG", "NWG", "BBD", "ITUB",
        "PSEC", "MAIN", "ARCC", "GBDC", "TPVG", "CSWC", "HTGC", "GLAD", "GAIN", "PFLT",
        "OWL", "CG", "KKR", "APO", "ARES", "BX", "BAM", "BN", "BROOKFIELD", "FIG",
        "TPG", "HLNE", "STEP", "BSIG", "AMG", "JHG", "VRTS", "APAM", "CNS", "FHI",
        "WBS", "ZION", "CMA", "SNV", "FHN", "WAL", "NYCB", "PACW", "SBNY", "FRC",
    ],
    "Industrials Extra": [
        "GEV", "VLTO", "SYM", "BE", "ENVX", "QS", "CHPT", "BLNK", "EVGO", "LCID",
        "RIVN", "NIO", "XPEV", "LI", "PSNY", "GOEV", "RIDE", "WKHS", "HYLN", "FREY",
        "OSK", "TEX", "AGCO", "CNHI", "DE", "CAT", "PCAR", "WAB", "GBX", "TRN",
        "RAIL", "FSTR", "ASTE", "MTW", "HY", "ALG", "LNN", "TWI", "SITE", "SHYF",
        "GVA", "TPC", "STRL", "MYRG", "AGX", "PRIM", "ROAD", "IESC", "FIX", "EME",
        "WCC", "AIT", "DXPE", "MSM", "GIC", "DSGR", "BECN", "BLDR", "BCC", "BUILD",
    ],
    "Consumer Extra": [
        "CHWY", "FRPT", "WOOF", "PETS", "ZTS", "IDXX", "TRUP", "GROOM", "BARK", "PETQ",
        "CELH", "MNST", "KO", "PEP", "KDP", "FIZZ", "COKE", "REED", "OTLY", "BYND",
        "TTCF", "SMPL", "POST", "LANC", "THS", "JJSF", "FLO", "BGS", "HAIN", "SENEA",
        "WING", "SHAK", "CMG", "DPZ", "YUM", "QSR", "JACK", "TAST", "LOCO", "DENN",
        "PLAY", "BJRI", "CAKE", "EAT", "CBRL", "DIN", "RUTH", "BLMN", "TXRH", "DRI",
        "BIRK", "CROX", "SKX", "WWW", "CAL", "SHOO", "BOOT", "VRA", "GIII", "COLM",
    ],
}


def _normalize_sector(sector: str) -> str:
    if sector.startswith("Basic Materials"):
        return "Materials"
    for prefix in (
        "Technology",
        "Healthcare",
        "Energy",
        "Financials",
        "Industrials",
        "Consumer",
        "Communication",
        "Utilities",
        "Real Estate",
        "Materials",
    ):
        if sector.startswith(prefix):
            if prefix == "Consumer":
                # Consumer Extra -> Consumer Cyclical (default bucket for mixed extras)
                return "Consumer Cyclical" if "Extra" in sector else sector
            if prefix == "Communication":
                return "Communication Services"
            return prefix
    return sector


def all_tickers_with_sector() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for sector, tickers in SECTOR_TICKERS.items():
        clean_sector = _normalize_sector(sector)
        for t in tickers:
            mapping.setdefault(t.upper().replace(".", "-"), clean_sector)
    return mapping


def tickers_for_limit(limit: int | None = None) -> list[str]:
    """Round-robin across sectors so small universes stay diversified."""
    mapping = all_tickers_with_sector()
    if limit is None or limit >= len(mapping):
        return list(mapping.keys())

    by_sector: dict[str, list[str]] = {}
    for sym, sector in mapping.items():
        by_sector.setdefault(sector, []).append(sym)

    ordered: list[str] = []
    queues = {s: list(syms) for s, syms in by_sector.items()}
    while len(ordered) < limit and queues:
        empty: list[str] = []
        for sector in list(queues.keys()):
            if not queues[sector]:
                empty.append(sector)
                continue
            ordered.append(queues[sector].pop(0))
            if len(ordered) >= limit:
                break
        for sector in empty:
            queues.pop(sector, None)
    return ordered


def sector_of(symbol: str, fallback: str = "Unknown") -> str:
    return all_tickers_with_sector().get(symbol.upper().replace(".", "-"), fallback)
