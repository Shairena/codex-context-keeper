using System.Reflection;
using System.Text.Json;
using System.Text.Json.Nodes;
using Tools.Types;

// This bridge is explicitly NOT atomic-safe against concurrent user input.
var output = new JsonSerializerOptions { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
void Emit(object value) => Console.WriteLine(JsonSerializer.Serialize(value, output));
if (args.Length < 3 || args[0] is not ("inspect" or "compact") || !Guid.TryParse(args[1], out _))
{ Emit(new { disposition="not_started", detail="Invalid bridge arguments." }); return 2; }
var tid = args[1]; var origin = args[2]; var mode = args[0];
try
{
    var home = Environment.GetEnvironmentVariable("CODEX_HOME") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".codex");
    var protectionFile = Path.Combine(home, "context-keeper-protected-threads.json");
    if (File.Exists(protectionFile))
    {
        var protectedIds = JsonSerializer.Deserialize<string[]>(File.ReadAllText(protectionFile)) ?? throw new FormatException();
        if (protectedIds.Any(id => !Guid.TryParse(id, out _))) throw new FormatException();
        if (protectedIds.Any(id => Guid.Parse(id) == Guid.Parse(tid)))
        { Emit(new { disposition="not_started", detail="Thread excluded by local protection settings." }); return 2; }
    }
}
catch
{ Emit(new { disposition="not_started", detail="Invalid local protection settings; operation stopped." }); return 2; }
if (mode == "compact" && (args.Length != 5 || args[4] != "--accept-known-race"))
{ Emit(new { disposition="not_started", detail="Explicit risk flag and expected owner required." }); return 2; }
bool mutationPossible = false;
using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(mode == "inspect" ? 25 : 110));
try
{
    await using var stream = new CodexContextStream(tid);
    var before = await stream.ReadAsync(timeout.Token);
    bool OriginCompleted()
    {
        var state = typeof(CodexContextStream).GetField("state", BindingFlags.Instance|BindingFlags.NonPublic)?.GetValue(stream) as JsonObject;
        IEnumerable<JsonNode?> turns = state?["turns"]?.AsArray() ?? Enumerable.Empty<JsonNode?>();
        if (state?["turnHistory"]?["kind"]?.GetValue<string>() == "canonical")
            turns = state?["turnHistory"]?["history"]?["entitiesByKey"]?.AsObject().Select(pair => pair.Value) ?? Enumerable.Empty<JsonNode?>();
        return turns.OfType<JsonObject>().Any(t => (t["turnId"]?.GetValue<string>() ?? t["id"]?.GetValue<string>()) == origin && t["status"]?.GetValue<string>() == "completed");
    }
    var originCompleted = OriginCompleted();
    if (mode == "inspect")
    { Emit(new { disposition="observed", threadId=tid, before.OwnerId, before.Idle, originCompleted, before.Revision }); return 0; }
    if (before.OwnerId != args[3] || !before.Idle || !originCompleted || before.UsedTokens is null or <= 0)
    { Emit(new { disposition="not_started", detail="Owner/idle/completed origin/usable metric changed or unavailable.", threadId=tid }); return 0; }
    var old = before.Compactions.Select(c=>c.ItemId).ToHashSet();
    mutationPossible = true; // Persisted Python intent precedes this call; never retry an ambiguous outcome.
    var after = await stream.CompactAsync(1, TimeSpan.FromSeconds(85), timeout.Token);
    var fresh = after.Compactions.Where(c=>!old.Contains(c.ItemId) && Guid.TryParse(c.ItemId,out _) && Guid.TryParse(c.TurnId,out _)).ToArray();
    if (fresh.Length == 0 && (!after.Idle || after.UsedTokens is null or <= 0))
    { Emit(new { disposition="not_started", detail="Reference stream skipped start on its last active/metric check." }); return 0; }
    bool completed = after.OwnerId == before.OwnerId && after.Idle && fresh.Any(c=>c.ItemCompleted && c.TurnStatus=="completed" && c.Error is null);
    Emit(new { disposition=completed?"completed":"ambiguous", threadId=tid, after.OwnerId, beforeUsedTokens=before.UsedTokens,
        afterUsedTokens=after.UsedTokens, compactions=fresh, knownRaceUnsafe=true,
        detail=completed?"New completed compaction item, completed turn and idle confirmed. Concurrent-input safety NOT guaranteed.":"Completion not established; automatic retry forbidden." });
    return completed?0:1;
}
catch(Exception ex)
{ Emit(new { disposition=mutationPossible?"ambiguous":"not_started", detail=ex.GetType().Name+": "+ex.Message, knownRaceUnsafe=true }); return 1; }
