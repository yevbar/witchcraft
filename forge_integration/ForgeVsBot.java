// ForgeVsBot.java — a REAL game: our Python engine (witchcraft) plays a seat vs Forge's AI, Forge owning
// the state. Forge asks our seat which spell/ability to play (chooseSpellAbilityToPlay); we forward the
// board + the legal plays to the Python bot (forge_bridge.serve + EnginePolicy) over a socket and return
// its pick. Every OTHER decision (targets, blocks, mulligan, mana) falls back to Forge's own AI — so this
// tests exactly "can our engine provide and play a valid main-phase move that Forge accepts" (completeness).
//
// Compile: javac -cp $FATJAR -d out forge_integration/ForgeVsBot.java
// Run:     (python: python3 -c 'import forge_bridge,...; serve(EnginePolicy())' on $PORT)
//          java -Djava.awt.headless=true -DbotHost=127.0.0.1 -DbotPort=$PORT -cp $FATJAR:out ForgeVsBot

import com.google.common.collect.Lists;

import forge.GuiDesktop;
import forge.LobbyPlayer;
import forge.ai.ComputerUtilAbility;
import forge.ai.ComputerUtilCost;
import forge.ai.LobbyPlayerAi;
import forge.ai.PlayerControllerAi;
import forge.deck.Deck;
import forge.game.Game;
import forge.game.GameRules;
import forge.game.GameType;
import forge.game.Match;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.player.Player;
import forge.game.player.RegisteredPlayer;
import forge.game.spellability.SpellAbility;
import forge.game.zone.ZoneType;
import forge.gui.GuiBase;
import forge.item.PaperCard;
import forge.localinstance.properties.ForgePreferences.FPref;
import forge.model.FModel;

import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.io.PrintWriter;
import java.net.Socket;
import java.util.ArrayList;
import java.util.List;

public class ForgeVsBot {
    static final String ASSETS = System.getenv().getOrDefault("FORGE_ASSETS",
            new File("forge-gui").getAbsolutePath() + File.separator);
    static final String HOST = System.getProperty("botHost", "127.0.0.1");
    static final int PORT = Integer.parseInt(System.getProperty("botPort", "8765"));

    // ---------- minimal JSON ----------
    static String esc(String s) {
        StringBuilder b = new StringBuilder();
        for (char c : s.toCharArray()) {
            if (c == '"' || c == '\\') b.append('\\').append(c);
            else if (c == '\n') b.append("\\n");
            else b.append(c);
        }
        return b.toString();
    }

    // ---------- the engine-consulting controller ----------
    static class RemoteController extends PlayerControllerAi {
        private final String seat;
        private Socket sock;
        private BufferedReader in;
        private PrintWriter out;
        private boolean greeted = false;

        RemoteController(Game game, Player p, LobbyPlayer lp) {
            super(game, p, lp);
            this.seat = p.getName();
        }

        private boolean connect() {
            if (sock != null) return true;
            try {
                sock = new Socket(HOST, PORT);
                sock.setSoTimeout(15000);
                in = new BufferedReader(new InputStreamReader(sock.getInputStream()));
                out = new PrintWriter(sock.getOutputStream(), true);
                send("{\"type\":\"hello\",\"you\":\"" + esc(seat) + "\",\"players\":" + playersJson() + "}");
                in.readLine();                                  // consume the "ready" reply
                greeted = true;
                return true;
            } catch (Exception e) {
                System.out.println("[bot] connect failed (" + e + "); falling back to Forge AI");
                sock = null;
                return false;
            }
        }

        private void send(String line) { out.print(line + "\n"); out.flush(); }

        private String playersJson() {
            StringBuilder b = new StringBuilder("[");
            List<Player> ps = getGame().getPlayers();
            for (int i = 0; i < ps.size(); i++) b.append(i > 0 ? "," : "").append('"').append(esc(ps.get(i).getName())).append('"');
            return b.append("]").toString();
        }

        private String cardJson(Card c) {
            return "{\"id\":\"" + c.getId() + "\",\"name\":\"" + esc(c.getName())
                    + "\",\"controller\":\"" + esc(c.getController().getName())
                    + "\",\"tapped\":" + c.isTapped() + "}";
        }

        private String observeJson() {
            Game g = getGame();
            StringBuilder b = new StringBuilder("{\"type\":\"observe\",\"state\":{");
            // life
            b.append("\"life\":{");
            List<Player> ps = g.getPlayers();
            for (int i = 0; i < ps.size(); i++)
                b.append(i > 0 ? "," : "").append('"').append(esc(ps.get(i).getName())).append("\":").append(ps.get(i).getLife());
            b.append("},");
            // active + step (main phases mapped; everything else -> a generic priority step name)
            String active = g.getPhaseHandler().getPlayerTurn() != null ? g.getPhaseHandler().getPlayerTurn().getName() : seat;
            String phase = String.valueOf(g.getPhaseHandler().getPhase());
            String step = phase.contains("MAIN2") ? "postcombat_main" : "precombat_main";
            b.append("\"active\":\"").append(esc(active)).append("\",\"step\":\"").append(step).append("\",");
            // zones: all battlefield permanents + OUR hand (opponent hand is hidden / irrelevant)
            b.append("\"zones\":{\"battlefield\":[");
            int n = 0;
            for (Card c : g.getCardsIn(ZoneType.Battlefield)) b.append(n++ > 0 ? "," : "").append(cardJson(c));
            b.append("],\"hand\":[");
            n = 0;
            for (Card c : getPlayer().getCardsIn(ZoneType.Hand)) b.append(n++ > 0 ? "," : "").append(cardJson(c));
            b.append("]}}}");
            return b.toString();
        }

        private List<SpellAbility> candidates() {
            List<SpellAbility> out = new ArrayList<>();
            try {
                CardCollection avail = ComputerUtilAbility.getAvailableCards(getGame(), getPlayer());
                List<SpellAbility> base = ComputerUtilAbility.getSpellAbilities(avail, getPlayer());
                for (SpellAbility sa : ComputerUtilAbility.getOriginalAndAltCostAbilities(base, getPlayer())) {
                    try {
                        if (sa.canPlay() && !sa.isManaAbility()
                                && ComputerUtilCost.canPayCost(sa, getPlayer(), false)) out.add(sa);
                    } catch (Throwable t) { /* skip */ }
                }
            } catch (Throwable t) { /* fall through to empty */ }
            return out;
        }

        @Override
        public List<SpellAbility> chooseSpellAbilityToPlay() {
            List<SpellAbility> cands = candidates();
            if (cands.isEmpty()) return null;                   // nothing to do -> pass (no need to consult)
            if (!greeted && !connect()) return super.chooseSpellAbilityToPlay();
            try {
                // options: each candidate carries the host-card id (for the engine's can_cast match) + its
                // index (ci, for mapping the reply back to the SpellAbility); plus a pass option.
                StringBuilder opts = new StringBuilder("[");
                for (int i = 0; i < cands.size(); i++) {
                    Card host = cands.get(i).getHostCard();
                    opts.append(i > 0 ? "," : "")
                        .append("{\"id\":\"").append(host != null ? host.getId() : 0)
                        .append("\",\"ci\":").append(i)
                        .append(",\"label\":\"").append(esc(cands.get(i).toString()))
                        .append("\",\"kind\":\"spell\"}");
                }
                opts.append(",{\"id\":\"0\",\"ci\":-1,\"label\":\"pass\",\"kind\":\"pass\"}]");
                send(observeJson());
                send("{\"type\":\"decide\",\"id\":1,\"kind\":\"action\",\"options\":" + opts
                        + ",\"default\":{\"id\":\"0\",\"ci\":-1,\"label\":\"pass\",\"kind\":\"pass\"}}");
                String reply = in.readLine();
                int ci = parseCi(reply);
                if (ci >= 0 && ci < cands.size()) {
                    System.out.println("[bot] engine plays: " + cands.get(ci) + "  (of " + cands.size() + " options)");
                    return Lists.newArrayList(cands.get(ci));
                }
                return null;                                    // engine passed
            } catch (Exception e) {
                System.out.println("[bot] decide failed (" + e + "); deferring to Forge AI");
                return super.chooseSpellAbilityToPlay();
            }
        }

        private int parseCi(String reply) {
            if (reply == null) return -1;
            java.util.regex.Matcher m = java.util.regex.Pattern.compile("\"ci\"\\s*:\\s*(-?\\d+)").matcher(reply);
            return m.find() ? Integer.parseInt(m.group(1)) : -1;
        }
    }

    // ---------- a LobbyPlayer that installs the RemoteController ----------
    static class RemotePlayer extends LobbyPlayerAi {
        RemotePlayer(String name) { super(name, null); }
        @Override public Player createIngamePlayer(Game game, int id) {
            Player p = new Player(getName(), game, id);
            p.setFirstController(new RemoteController(game, p, this));
            return p;
        }
        @Override public forge.game.player.PlayerController createMindSlaveController(Player master, Player slave) {
            return new RemoteController(slave.getGame(), slave, this);
        }
    }

    // ---------- setup ----------
    static void initForge() {
        GuiBase.setInterface(new GuiDesktop() { @Override public String getAssetsDir() { return ASSETS; } });
        FModel.initialize(null, prefs -> {
            prefs.setPref(FPref.LOAD_CARD_SCRIPTS_LAZILY, false);
            prefs.setPref(FPref.UI_LANGUAGE, "en-US");
            return null;
        });
    }

    static PaperCard card(String name) {
        PaperCard c = FModel.getMagicDb().getCommonCards().getCard(name);
        if (c == null) throw new RuntimeException("card not found: " + name);
        return c;
    }

    static Deck gruul(String name) {
        Deck d = new Deck(name);
        d.getMain().add(card("Forest"), 30);
        d.getMain().add(card("Mountain"), 14);
        d.getMain().add(card("Grizzly Bears"), 4);
        d.getMain().add(card("Gray Ogre"), 4);
        d.getMain().add(card("Hill Giant"), 4);
        d.getMain().add(card("Craw Wurm"), 4);
        return d;
    }

    public static void main(String[] args) {
        initForge();
        List<RegisteredPlayer> players = Lists.newArrayList();
        players.add(new RegisteredPlayer(gruul("witch")).setPlayer(new RemotePlayer("Witchcraft-Engine")));
        players.add(new RegisteredPlayer(gruul("forge")).setPlayer(new LobbyPlayerAi("Forge-AI", null)));
        GameRules rules = new GameRules(GameType.Constructed);
        rules.setGamesPerMatch(1);
        Match match = new Match(rules, players, "witchcraft-vs-forge");
        Game game = new Game(players, rules, match);
        System.out.println("Starting: Witchcraft-Engine (our datalog engine via Python) vs Forge-AI ...");
        long t0 = System.currentTimeMillis();
        match.startGame(game);
        String w = (game.getOutcome() != null && game.getOutcome().getWinningLobbyPlayer() != null)
                ? game.getOutcome().getWinningLobbyPlayer().getName() : "draw/none";
        System.out.println("RESULT winner=" + w + " turns=" + game.getPhaseHandler().getTurn()
                + " wall=" + (System.currentTimeMillis() - t0) + "ms");
    }
}
