using System;
using BoostGateway.Sdk;

static void Check(bool ok, string message)
{
    if (!ok)
    {
        Console.Error.WriteLine("FAIL: " + message);
        Environment.Exit(1);
    }
}

var host = args.Length > 0 ? args[0] : "127.0.0.1";
var port = args.Length > 1 ? ushort.Parse(args[1]) : (ushort)9201;

Console.WriteLine($"BoostGateway C# SDK full flow: {host}:{port}");
Console.WriteLine($"Native SDK: {SdkClient.Version}");

using var alice = new SdkClient();
using var bob = new SdkClient();
var runId = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds().ToString();
var aliceId = "alice_cs_" + runId;
var bobId = "bob_cs_" + runId;
var roomId = "cs_room_" + runId;
var baseScore = 9_000_000_000_000L + (DateTimeOffset.UtcNow.ToUnixTimeMilliseconds() % 1_000_000L);

Check(alice.Connect(host, port), "alice connect");
Check(bob.Connect(host, port), "bob connect");

alice.StartHeartbeat(15);
bob.StartHeartbeat(15);

var loginAlice = alice.Login(aliceId, "token:" + aliceId);
Check(loginAlice.Ok != 0, "alice login: " + loginAlice.ErrorMessage);

var loginBob = bob.Login(bobId, "token:" + bobId);
Check(loginBob.Ok != 0, "bob login: " + loginBob.ErrorMessage);

var echo = alice.Echo("hello from csharp");
Check(echo.Ok != 0 && echo.Body.Contains("hello"), "echo");

var matchJoin = alice.MatchJoin(aliceId, 1200, "1v1");
Check(matchJoin.Ok != 0, "alice match join: " + matchJoin.ErrorMessage);
var matchStatus = alice.MatchStatus(aliceId, "1v1");
Check(matchStatus.Ok != 0, "alice match status: " + matchStatus.ErrorMessage);
var bobMatchJoin = bob.MatchJoin(bobId, 1210, "1v1");
Check(bobMatchJoin.Ok != 0, "bob match join: " + bobMatchJoin.ErrorMessage);
System.Threading.Thread.Sleep(1200);
var bobMatchLeave = bob.MatchLeave(bobId, "1v1");
Check(bobMatchLeave.Ok != 0, "bob match leave: " + bobMatchLeave.ErrorMessage);

var room = alice.CreateRoom(roomId);
Check(room.Ok != 0, "create room: " + room.ErrorMessage);
Check(bob.JoinRoom(roomId).Ok != 0, "bob join room");
Check(alice.SetReady(true).Ok != 0, "alice ready");
Check(bob.SetReady(true).Ok != 0, "bob ready");

var battle = alice.StartBattle(roomId);
Check(battle.Ok != 0, "start battle: " + battle.ErrorMessage);
Check(alice.SendBattleInput("move:10,20").Ok != 0, "alice battle input");

var top = alice.LeaderboardTop(20);
Check(top.Ok != 0 && top.ResponseBody.Contains(aliceId) && top.ResponseBody.Contains(bobId), "auto settlement leaderboard top: " + top.ResponseBody);
var rank = alice.LeaderboardRank(aliceId);
Check(rank.Ok != 0 && rank.ResponseBody.Contains(aliceId), "auto settlement leaderboard rank: " + rank.ResponseBody);
var aliceSubmit = alice.LeaderboardSubmit(aliceId, "Alice", baseScore);
Check(aliceSubmit.Ok != 0, "manual alice leaderboard submit: " + aliceSubmit.ErrorMessage);
var bobSubmit = bob.LeaderboardSubmit(bobId, "Bob", baseScore + 100);
Check(bobSubmit.Ok != 0, "manual bob leaderboard submit: " + bobSubmit.ErrorMessage);

alice.StopHeartbeat();
bob.StopHeartbeat();
alice.Disconnect();
bob.Disconnect();

Console.WriteLine("C# SDK full flow completed.");
