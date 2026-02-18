class RiskManager:
    def __init__(self, total_bankroll: float, kelly_fraction: float = 0.25):
        """
        :param total_bankroll: Total USD you are willing to trade with.
        :param kelly_fraction: 'Fractional Kelly' to reduce risk (0.25 is recommended).
        """
        self.bankroll = total_bankroll
        self.fraction = kelly_fraction

    def calculate_bet(self, market_price: float, user_prob: float) -> dict:
        """
        Calculates optimal bet size using Kelly Criterion.
        Formula: f* = (bp - q) / b
        """
        p = user_prob / 100.0  # Your estimated probability (e.g., 0.90)
        q = 1.0 - p            # Probability of losing
        
        # 'b' is the net odds (decimal odds - 1)
        # On Polymarket: b = (1 - price) / price
        if market_price <= 0 or market_price >= 1:
            return {"amount": 0, "status": "Invalid Price"}
            
        b = (1.0 - market_price) / market_price
        
        # Full Kelly %
        raw_f = (b * p - q) / b
        
        # Apply Fractional Kelly & ensure we don't bet if no edge (raw_f <= 0)
        optimal_f = max(0, raw_f * self.fraction)
        
        # Limit single bet to 10% of bankroll as an extra safety cap
        safe_f = min(optimal_f, 0.10)
        
        suggested_usd = self.bankroll * safe_f
        
        return {
            "suggested_usd": round(suggested_usd, 2),
            "percentage": round(safe_f * 100, 2),
            "edge": round((p - market_price) * 100, 2)
        }