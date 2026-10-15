using System.Text.Json.Nodes;

namespace Tools.Types;

// A live owner observation. Total token expenditure is deliberately not exposed.
internal sealed record CodexContextSnapshot(string ThreadId, string OwnerId, long Revision,
    string Status, long? UsedTokens, long? ContextWindow, IReadOnlyList<CodexCompaction> Compactions)
{
    public bool Idle => Status == "idle";

    internal static CodexContextSnapshot Read(JsonObject state, string owner, long revision)
    {
        var thread = state["id"]?.GetValue<string>() ?? throw new InvalidOperationException("Desktop state has no thread ID.");
        var status = state["threadRuntimeStatus"]?["type"]?.GetValue<string>() ?? "unavailable";
        IEnumerable<JsonNode?> turns = state["turns"]?.AsArray() ?? Enumerable.Empty<JsonNode?>();
        if (state["turnHistory"]?["kind"]?.GetValue<string>() == "canonical")
            turns = state["turnHistory"]?["history"]?["entitiesByKey"]?.AsObject().Select(pair => pair.Value)
                ?? Enumerable.Empty<JsonNode?>();
        var compactions = new List<CodexCompaction>();
        foreach (var turn in turns.OfType<JsonObject>())
        {
            var turnStatus = turn["status"]?.GetValue<string>() ?? "unknown";
            if (turnStatus == "inProgress") status = "active";
            foreach (var item in (turn["items"]?.AsArray() ?? new JsonArray()).OfType<JsonObject>())
            {
                if (item["type"]?.GetValue<string>() != "contextCompaction") continue;
                var id = item["id"]?.GetValue<string>() ?? throw new InvalidOperationException("Compaction item has no ID.");
                compactions.Add(new(id, turn["turnId"]?.GetValue<string>() ?? "", turnStatus,
                    item["completed"]?.GetValue<bool>() == true || turn["completedItemIds"]?[id]?.GetValue<bool>() == true,
                    turn["error"]?.ToJsonString()));
            }
        }
        if ((state["unconfirmedTurnSubmissions"]?.AsArray() ?? new JsonArray())
            .Any(item => item?["terminal"]?.GetValue<bool>() != true)) status = "active";
        var usage = state["resumeState"]?.GetValue<string>() == "resumed" ? state["latestTokenUsageInfo"] : null;
        long? used = usage?["last"]?["totalTokens"]?.GetValue<long>();
        long? window = usage?["modelContextWindow"]?.GetValue<long>();
        if (used < 0) used = null;
        if (window <= 0) window = null;
        if (window is null) used = null;
        // Same occupied-context value displayed by Desktop, not usage.total.
        if (used is not null && window is not null) used = Math.Min(used.Value, window.Value);
        return new(thread, owner, revision, status, used, window, compactions);
    }
}

internal sealed record CodexCompaction(string ItemId, string TurnId, string TurnStatus, bool ItemCompleted, string? Error);
