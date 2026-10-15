using System.Buffers.Binary;
using System.IO.Pipes;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Tools.Types;

// Desktop owner/follower IPC. Never starts or resumes an independent App Server.
// Each operation owns one observer lease and releases it, including cancellation.
internal sealed class CodexContextStream : IAsyncDisposable
{
    private readonly NamedPipeClientStream pipe;
    private readonly string threadId;
    private string clientId = "initializing-client";
    private string ownerId = "";
    private JsonObject? state;
    private long revision = -1;
    private long sequence;

    public CodexContextStream(string threadId) : this(threadId, "codex-ipc") { }
    internal CodexContextStream(string threadId, string pipeName)
    {
        this.threadId = threadId;
        pipe = new(".", pipeName, PipeDirection.InOut, PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly);
    }

    public async Task<CodexContextSnapshot> ReadAsync(CancellationToken token)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
        timeout.CancelAfter(TimeSpan.FromSeconds(15));
        try
        {
            if (!pipe.IsConnected)
            {
                await pipe.ConnectAsync(timeout.Token);
                var initialized = await RequestAsync("initialize", new { clientType = "task-overseer" }, 0, null, timeout.Token);
                clientId = initialized["result"]?["clientId"]?.GetValue<string>() ?? throw new InvalidOperationException("Desktop IPC did not initialize.");
                var discovered = await RequestAsync("thread-owner-discovery", new { hostId = "local", conversationId = threadId }, 1, null, timeout.Token);
                ownerId = discovered["handledByClientId"]?.GetValue<string>() ?? throw new InvalidOperationException("No live Desktop owner for the target chat.");
            }
            await FollowAsync(true, timeout.Token);
            while (true)
            {
                var message = await ReadFrameAsync(timeout.Token);
                if (AcceptState(message) && message["params"]?["change"]?["type"]?.GetValue<string>() == "snapshot")
                    return Snapshot();
                await RejectDiscoveryAsync(message, timeout.Token);
            }
        }
        catch (OperationCanceledException) when (!token.IsCancellationRequested)
        { throw new TimeoutException("Timed out reading the target chat's live Desktop context."); }
    }

    public async Task<CodexContextSnapshot> CompactAsync(long threshold, TimeSpan timeoutDuration, CancellationToken token)
    {
        var before = await ReadAsync(token);
        if (before.Status == "active") return before; // Caller waits for idle; no compact RPC was issued.
        if (!before.Idle) throw new InvalidOperationException("The target chat's idle state is unavailable; compaction was not started.");
        if (before.UsedTokens is null) return before; // Optional metric disappeared: no compact RPC is needed.
        if (before.UsedTokens < threshold) return before;
        var existing = before.Compactions.Select(item => item.ItemId).ToHashSet(StringComparer.Ordinal);
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
        timeout.CancelAfter(timeoutDuration);
        try
        {
            // This Desktop handler forwards thread/compact/start to its own live App Server.
            var accepted = await RequestAsync("thread-follower-compact-thread", new { conversationId = threadId }, 1, ownerId, timeout.Token);
            if (accepted["handledByClientId"]?.GetValue<string>() != ownerId || accepted["result"]?["ok"]?.GetValue<bool>() != true)
                throw new InvalidOperationException("Desktop did not accept compaction on the identified owner.");
            while (true)
            {
                var current = Snapshot();
                var newItems = current.Compactions.Where(item => !existing.Contains(item.ItemId)).ToArray();
                var failure = newItems.FirstOrDefault(item => item.TurnStatus is "failed" or "interrupted" || item.Error is not null);
                if (failure is not null) throw new InvalidOperationException("Compaction failed: " + (failure.Error ?? failure.TurnStatus));
                if (newItems.Any(item => item.ItemCompleted && item.TurnStatus == "completed") && current.Idle)
                    return current;
                var message = await ReadFrameAsync(timeout.Token);
                _ = AcceptState(message);
                await RejectDiscoveryAsync(message, timeout.Token);
            }
        }
        catch (OperationCanceledException) when (!token.IsCancellationRequested)
        { throw new TimeoutException("Desktop accepted compaction, but its completed item, completed turn and idle state were not confirmed before timeout."); }
    }

    public async Task StartUserTurnAsync(string prompt, CancellationToken token)
    {
        if (string.IsNullOrWhiteSpace(prompt)) throw new ArgumentException("A user turn requires input text.", nameof(prompt));
        var before = await ReadAsync(token);
        if (!before.Idle) throw new InvalidOperationException("The target chat is not idle; a user turn was not started.");
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(token);
        timeout.CancelAfter(TimeSpan.FromSeconds(30));
        try
        {
            // Desktop's owner prepares current workspace/model/permission settings and calls
            // its own thread's turn/start. No delegation envelope, separate CLI or overrides.
            // Local start-turn requests use version 2 (no outer hostId), independently of
            // discovery/compact version 1 and the context snapshot's version 11.
            var accepted = await RequestAsync("thread-follower-start-turn", new
            {
                conversationId = threadId,
                turnStart = new {
                    request = new { threadId, input = new[] { new { type = "text", text = prompt, text_elements = Array.Empty<object>() } } },
                    context = new { inheritThreadSettings = true }
                }
            }, 2, ownerId, timeout.Token);
            if (accepted["handledByClientId"]?.GetValue<string>() != ownerId ||
                string.IsNullOrWhiteSpace(accepted["result"]?["result"]?["turn"]?["id"]?.GetValue<string>()))
                throw new InvalidOperationException("Desktop did not confirm a user turn on the identified owner.");
        }
        catch (OperationCanceledException) when (!token.IsCancellationRequested)
        { throw new TimeoutException("Native user-turn acceptance was not confirmed. Do not resend blindly."); }
    }

    private CodexContextSnapshot Snapshot() => state is null
        ? throw new InvalidOperationException("No live Desktop snapshot.") : CodexContextSnapshot.Read(state, ownerId, revision);

    private async Task<JsonObject> RequestAsync(string method, object parameters, int version, string? target, CancellationToken token)
    {
        var id = (++sequence).ToString(System.Globalization.CultureInfo.InvariantCulture);
        await WriteFrameAsync(new { type = "request", requestId = id, sourceClientId = clientId, version,
            method, @params = parameters, targetClientId = target, timeoutMs = 10000 }, token);
        while (true)
        {
            var message = await ReadFrameAsync(token);
            if (message["type"]?.GetValue<string>() == "response" && message["requestId"]?.GetValue<string>() == id)
            {
                if (message["resultType"]?.GetValue<string>() != "success")
                    throw new InvalidOperationException("Desktop IPC " + method + ": " + message["error"]?.GetValue<string>());
                return message;
            }
            _ = AcceptState(message);
            await RejectDiscoveryAsync(message, token);
        }
    }

    private Task FollowAsync(bool following, CancellationToken token) => WriteFrameAsync(new
    {
        type = "broadcast", method = "thread-stream-following-changed", sourceClientId = clientId, version = 1,
        targetClientIds = new[] { ownerId }, @params = new { hostId = "local", conversationId = threadId, following }
    }, token);

    private bool AcceptState(JsonObject message)
    {
        if (message["type"]?.GetValue<string>() != "broadcast") return false;
        if (message["method"]?.GetValue<string>() == "client-status-changed" &&
            message["params"]?["clientId"]?.GetValue<string>() == ownerId && message["params"]?["status"]?.GetValue<string>() == "disconnected")
            throw new InvalidOperationException("The live Desktop thread owner disconnected.");
        if (message["method"]?.GetValue<string>() != "thread-stream-state-changed" ||
            message["params"]?["conversationId"]?.GetValue<string>() != threadId || message["params"]?["hostId"]?.GetValue<string>() != "local") return false;
        if (message["sourceClientId"]?.GetValue<string>() != ownerId) return false;
        if (message["version"]?.GetValue<int>() != 11) throw new InvalidOperationException("Unsupported Desktop context-stream version.");
        var change = message["params"]!["change"]!.AsObject();
        var nextRevision = change["revision"]!.GetValue<long>();
        if (nextRevision < revision) throw new InvalidOperationException("Desktop context stream moved to an older revision; dispatch is blocked.");
        if (change["type"]?.GetValue<string>() == "snapshot")
        {
            state = change["conversationState"]?.AsObject() ?? throw new InvalidOperationException("Desktop snapshot is empty.");
            if (state["id"]?.GetValue<string>() != threadId) throw new InvalidOperationException("Desktop snapshot belongs to another chat.");
        }
        else if (change["type"]?.GetValue<string>() == "patches")
        {
            if (state is null || change["baseRevision"]?.GetValue<long>() != revision)
                throw new InvalidOperationException("Desktop context stream lost its revision; dispatch is blocked.");
            foreach (var patch in change["patches"]!.AsArray().OfType<JsonObject>()) ApplyPatch(state, patch);
        }
        else throw new InvalidOperationException("Unsupported Desktop state change.");
        revision = nextRevision;
        return true;
    }

    internal static void ApplyPatch(JsonObject root, JsonObject patch)
    {
        var path = patch["path"]!.AsArray();
        if (path.Count == 0) throw new InvalidOperationException("Unsupported root replacement in Desktop state.");
        JsonNode node = root;
        foreach (var part in path.Take(path.Count - 1))
            node = node is JsonArray array ? array[part!.GetValue<int>()]! : node[part!.GetValue<string>()]!;
        var last = path[^1]!;
        var op = patch["op"]!.GetValue<string>();
        if (op is not ("add" or "replace" or "remove")) throw new InvalidOperationException("Unsupported Desktop patch operation.");
        var value = patch["value"] is null ? null : JsonNode.Parse(patch["value"]!.ToJsonString());
        if (node is JsonArray list)
        {
            if (last is JsonValue key && key.TryGetValue<string>(out var text) && text == "length")
            {
                var length = value!.GetValue<int>();
                while (list.Count > length) list.RemoveAt(list.Count - 1);
                if (list.Count != length) throw new InvalidOperationException("Unsupported sparse Desktop array.");
            }
            else
            {
                var index = last.GetValue<int>();
                if (op == "add") list.Insert(index, value);
                else if (op == "remove") list.RemoveAt(index);
                else list[index] = value;
            }
        }
        else if (op == "remove") node.AsObject().Remove(last.GetValue<string>());
        else node[last.GetValue<string>()] = value;
    }

    private Task RejectDiscoveryAsync(JsonObject message, CancellationToken token) => message["type"]?.GetValue<string>() == "client-discovery-request"
        ? WriteFrameAsync(new { type = "client-discovery-response", requestId = message["requestId"]!.GetValue<string>(), response = new { canHandle = false } }, token)
        : Task.CompletedTask;

    private async Task WriteFrameAsync(object message, CancellationToken token)
    {
        var payload = JsonSerializer.SerializeToUtf8Bytes(message);
        var header = new byte[4]; BinaryPrimitives.WriteInt32LittleEndian(header, payload.Length);
        await pipe.WriteAsync(header, token); await pipe.WriteAsync(payload, token); await pipe.FlushAsync(token);
    }

    private async Task<JsonObject> ReadFrameAsync(CancellationToken token)
    {
        var header = new byte[4]; await pipe.ReadExactlyAsync(header, token);
        var length = BinaryPrimitives.ReadInt32LittleEndian(header);
        if (length <= 0 || length > 268435456) throw new InvalidOperationException("Invalid Desktop IPC frame length.");
        var payload = new byte[length]; await pipe.ReadExactlyAsync(payload, token);
        return JsonNode.Parse(payload)?.AsObject() ?? throw new InvalidOperationException("Empty Desktop IPC frame.");
    }

    public async ValueTask DisposeAsync()
    {
        try
        {
            if (pipe.IsConnected && ownerId.Length > 0)
            { using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(1)); await FollowAsync(false, timeout.Token); }
        }
        catch (Exception error) when (error is IOException or OperationCanceledException or ObjectDisposedException) { }
        finally { await pipe.DisposeAsync(); }
    }
}
