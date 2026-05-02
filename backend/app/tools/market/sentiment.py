def synthesize(coin: str, price_data: dict, news_items: list[dict], sentiment: dict) -> str:
    """
    Combine price trend, news headlines, and vote sentiment into a readable signal.
    Returns a 2-3 sentence paragraph for the LLM to include in its response.
    """
    change = price_data.get("change_24h", 0) if price_data else 0
    price_trend = "up" if change > 1 else ("down" if change < -1 else "flat")
    signal = sentiment.get("signal", "neutral")
    bull = sentiment.get("bullish_pct", 0)
    bear = sentiment.get("bearish_pct", 0)

    # Combine signals
    if price_trend == "up" and signal == "bullish":
        outlook = "bullish signals across the board"
    elif price_trend == "down" and signal == "bearish":
        outlook = "bearish pressure on both price and sentiment"
    elif price_trend == "up" and signal == "bearish":
        outlook = "price rising despite bearish news sentiment — watch for reversal"
    elif price_trend == "down" and signal == "bullish":
        outlook = "price dipping while news stays bullish — possible buy-the-dip setup"
    else:
        outlook = "mixed signals — no clear directional edge right now"

    parts = [f"Sentiment for **{coin.upper()}**: {outlook}."]

    if news_items:
        parts.append(f"News is {bull:.0f}% bullish / {bear:.0f}% bearish across {sentiment.get('news_count', len(news_items))} recent articles.")
        top = news_items[:3]
        headlines = "  ".join(f"• {n['title']}" for n in top)
        parts.append(f"Top headlines: {headlines}")
    else:
        parts.append("No recent news data available (add a CryptoPanic API key in Settings for live headlines).")

    return "\n".join(parts)
