

def update_elo(winner_elo: float, loser_elo: float, k: float) -> tuple[float, float]:
    """Standard Elo rating update."""
    expected_winner = 1.0 / (1.0 + 10.0 ** ((loser_elo - winner_elo) / 400.0))
    new_winner = winner_elo + k * (1.0 - expected_winner)
    new_loser = loser_elo + k * (expected_winner - 1.0)
    return new_winner, new_loser


def get_k_factor(comparisons: int, mode: str) -> float:
    """Dynamic K-factor based on comparison count and mode."""
    if mode == "topn":
        return 16.0
    if comparisons < 10:
        return 40.0
    return 20.0


