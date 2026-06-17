// ForgeBench.java — throughput benchmark for Forge's per-node search primitives.
//   GameCopier.makeCopy()             : deep-copy a game state (what Forge's AI pays per lookahead node)
//   GameStateEvaluator.getScore...    : the positional eval (analog of witchcraft's leaf eval)
// Two AI players play a real game; once a board has developed we grab the live Game and time the loop.
// Run: java -Dheadless=... -Ddeck0=.. -Ddeck1=.. -cp <fatjar>:<out> ForgeBench
import com.google.common.collect.Lists;
import com.google.common.eventbus.Subscribe;
import forge.gui.GuiBase;
import forge.GuiDesktop;
import forge.LobbyPlayer;
import forge.ai.LobbyPlayerAi;
import forge.ai.simulation.GameCopier;
import forge.ai.simulation.GameStateEvaluator;
import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.Match;
import forge.game.card.Card;
import forge.game.event.GameEventTurnPhase;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.zone.ZoneType;
import forge.model.FModel;
import forge.localinstance.properties.ForgePreferences.FPref;
import java.util.List;

public class ForgeBench {
    static final String ASSETS = System.getenv("FORGE_ASSETS");

    static void initForge() {
        GuiBase.setInterface(new GuiDesktop() { @Override public String getAssetsDir() { return ASSETS; } });
        FModel.initialize(null, prefs -> {
            prefs.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
            prefs.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });
    }

    static Deck loadDeck(String path, String name) throws Exception {
        Deck d = new Deck(name);
        String section = "main";
        for (String raw : java.nio.file.Files.readAllLines(java.nio.file.Paths.get(path))) {
            String line = raw.trim();
            if (line.isEmpty() || line.startsWith("[metadata]") || line.startsWith("Name=")) continue;
            if (line.equalsIgnoreCase("[Commander]")) { section = "commander"; continue; }
            if (line.equalsIgnoreCase("[Main]")) { section = "main"; continue; }
            int n = 1; String cname = line;
            java.util.regex.Matcher m = java.util.regex.Pattern.compile("^(\\d+)\\s+(.*)$").matcher(line);
            if (m.matches()) { n = Integer.parseInt(m.group(1)); cname = m.group(2).trim(); }
            var pc = FModel.getMagicDb().getCommonCards().getCard(cname);
            if (pc == null && cname.contains("//")) pc = FModel.getMagicDb().getCommonCards().getCard(cname.split("//")[0].trim());
            if (pc == null) continue;
            if (section.equals("commander")) d.getOrCreate(forge.deck.DeckSection.Commander).add(pc, n);
            else d.getMain().add(pc, n);
        }
        return d;
    }

    static boolean done = false;

    static class Bencher {
        final Game game;
        Bencher(Game game) { this.game = game; }
        @Subscribe public void onPhase(GameEventTurnPhase e) {
            if (done) return;
            int bf = game.getCardsIn(ZoneType.Battlefield).size();
            int turn = game.getPhaseHandler().getTurn();
            if (turn < 5 && bf < 10) return;                 // wait for a developed board
            done = true;
            run();
            System.exit(0);
        }
        void run() {
            Player p = game.getPlayers().get(0);
            int bf = game.getCardsIn(ZoneType.Battlefield).size();
            System.out.println("BENCH state: turn=" + game.getPhaseHandler().getTurn()
                    + " battlefield=" + bf + " totalCards=" + game.getCardsInGame().size());
            // warm
            GameStateEvaluator ev = new GameStateEvaluator();
            for (int i = 0; i < 5; i++) { new GameCopier(game).makeCopy(); ev.getScoreForGameState(game, p); }
            // 1) GameCopier.makeCopy() — the per-node state-copy primitive
            long t0 = System.nanoTime(); int n = 0;
            while (System.nanoTime() - t0 < 6_000_000_000L) { new GameCopier(game).makeCopy(); n++; }
            double secs = (System.nanoTime() - t0) / 1e9, r1 = n / secs;
            System.out.printf("[forge] GameCopier.makeCopy(): %8.1f states/sec  -> 1s=%.0f  5s=%.0f  10s=%.0f%n",
                    r1, r1, 5 * r1, 10 * r1);
            // 2) GameStateEvaluator — the eval primitive
            t0 = System.nanoTime(); n = 0;
            while (System.nanoTime() - t0 < 4_000_000_000L) { ev.getScoreForGameState(game, p); n++; }
            secs = (System.nanoTime() - t0) / 1e9; double r2 = n / secs;
            System.out.printf("[forge] GameStateEvaluator.getScore: %7.1f evals/sec -> 1s=%.0f  5s=%.0f  10s=%.0f%n",
                    r2, r2, 5 * r2, 10 * r2);
        }
    }

    public static void main(String[] args) throws Exception {
        initForge();
        List<RegisteredPlayer> players = Lists.newArrayList();
        for (int i = 0; i < 2; i++) {
            Deck deck = loadDeck(System.getProperty("deck" + i), "AI" + i);
            players.add(RegisteredPlayer.forCommander(deck).setPlayer(new LobbyPlayerAi("AI" + i, null)));
        }
        GameRules rules = new GameRules(GameType.Commander);
        rules.setGamesPerMatch(1);
        Match match = new Match(rules, players, "bench");
        Game game = new Game(players, rules, match);
        game.subscribeToEvents(new Bencher(game));
        System.out.println("ForgeBench: two AI seats play until a board develops, then benchmark...");
        match.startGame(game);
        if (!done) System.out.println("game ended before benchmark fired (no developed board reached)");
    }
}
