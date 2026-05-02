def synthesize(coin: str, price_data: dict, news_items: list[dict], sentiment: dict) -> str:
    change = price_data.get("change_24h", 0) if price_data else 0
    price_trend = "up" if change > 1 else ("down" if change < -1 else "flat")
    signal  = sentiment.get("signal", "neutral")
    bull    = sentiment.get("bullish_pct", 0)
    bear    = sentiment.get("bearish_pct", 0)
    fng_val = sentiment.get("fng_value")
    fng_lbl = sentiment.get("fng_label", "")

    if price_trend == "up" and signal == "bullish":
        outlook = "bullish signals across the board"
    elif price_trend == "down" and signal == "bearish":
        outlook = "bearish pressure on both price and sentiment"
    elif price_trend == "up" and signal == "bearish":
        outlook = "price rising despite bearish news — watch for reversal"
    elif price_trend == "down" and signal == "bullish":
        outlook = "price dipping while news stays bullish — possible buy-the-dip setup"
    else:
        outlook = "mixed signals — no clear directional edge right now"

    parts = [f"Sentiment for **{coin.upper()}**: {outlook}."]

    if fng_val is not None:
        parts.append(f"Market Fear & Greed: **{fng_val}/100 — {fng_lbl}**")

    if news_items:
        parts.append(f"Headlines are {bull:.0f}% bullish / {bear:.0f}% bearish across {sentiment.get('news_count', len(news_items))} articles.")
        headlines = "  ".join(f"• {n['title']}" for n in news_items[:3])
        parts.append(f"Top headlines: {headlines}")
    else:
        parts.append("No recent coin-specific headlines found.")

    return "\n".join(parts)
