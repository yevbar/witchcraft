// ForgeVsBot.java — a REAL game: our Python engine (witchcraft) plays a seat vs Forge's AI, Forge owning
// the state. PURE ENGINE: every STRATEGIC decision Forge asks our seat to make — which spell/land to play
// (chooseSpellAbilityToPlay), which creatures attack (declareAttackers), which block (declareBlockers),
// keep/mulligan (mulliganKeepHand) — is forwarded to the Python bot (forge_bridge.serve + EnginePolicy)
// over a socket; NONE of Forge's AI strategy is consulted (no super.* fallback — on any failure we take a
// safe LEGAL default: pass / no-attack / no-block / keep). Forge still does the MECHANICAL rules steps it
// owns (which lands to tap to pay a cost, combat damage assignment order) — those aren't AI strategy.
// The bot reports a completeness number: of the options Forge offers, how many our engine models+endorses.
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
import forge.game.GameEntity;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.combat.Combat;
import forge.game.combat.CombatUtil;
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

        // send one decision (observe + decide) and return the raw reply line, or null on failure. NO Forge-AI
        // fallback anywhere — if the engine can't answer, the caller takes a safe LEGAL default (pass / no
        // attack / no block / keep), so the game stays legal but Forge's AI strategy is never consulted.
        private String decide(String kind, String optionsJson, String defaultJson) {
            if (!greeted && !connect()) return null;
            try {
                send(observeJson());
                send("{\"type\":\"decide\",\"id\":1,\"kind\":\"" + kind + "\",\"options\":" + optionsJson
                        + ",\"default\":" + defaultJson + "}");
                return in.readLine();
            } catch (Exception e) { return null; }
        }

        private String valuePart(String r) {                    // the reply's "value" sub-string (avoid matching id)
            if (r == null) return "";
            int i = r.indexOf("\"value\"");
            return i < 0 ? r : r.substring(i);
        }

        private Card bfCard(int id) {
            for (Card c : getGame().getCardsIn(ZoneType.Battlefield)) if (c.getId() == id) return c;
            return null;
        }

        @Override
        public List<SpellAbility> chooseSpellAbilityToPlay() {
            List<SpellAbility> cands = candidates();
            if (cands.isEmpty()) return null;                   // nothing to do -> pass
            StringBuilder opts = new StringBuilder("[");
            for (int i = 0; i < cands.size(); i++) {            // each candidate: host-card id (engine match) + ci
                Card host = cands.get(i).getHostCard();
                opts.append(i > 0 ? "," : "")
                    .append("{\"id\":\"").append(host != null ? host.getId() : 0)
                    .append("\",\"ci\":").append(i)
                    .append(",\"label\":\"").append(esc(cands.get(i).toString())).append("\",\"kind\":\"spell\"}");
            }
            opts.append(",{\"id\":\"0\",\"ci\":-1,\"label\":\"pass\",\"kind\":\"pass\"}]");
            int ci = parseCi(decide("action", opts.toString(),
                    "{\"id\":\"0\",\"ci\":-1,\"label\":\"pass\",\"kind\":\"pass\"}"));
            if (ci >= 0 && ci < cands.size()) {
                System.out.println("[bot] engine plays: " + cands.get(ci) + "  (of " + cands.size() + " options)");
                return Lists.newArrayList(cands.get(ci));
            }
            return null;                                        // engine passed (or unreachable -> pass, NOT Forge AI)
        }

        @Override
        public boolean mulliganKeepHand(Player firstPlayer, int cardsToReturn) {
            String r = valuePart(decide("mulligan", "[true,false]", "true"));
            return !r.contains("false");                        // engine decides keep/mull (default keep)
        }

        @Override
        public void declareAttackers(Player attacker, Combat combat) {
            try {
                if (combat.getDefendingPlayers().isEmpty()) return;
                GameEntity def = combat.getDefendingPlayers().get(0);
                List<Card> elig = new java.util.ArrayList<>();
                for (Card c : attacker.getCreaturesInPlay()) if (CombatUtil.canAttack(c, def)) elig.add(c);
                if (elig.isEmpty()) return;
                StringBuilder ids = new StringBuilder("[");
                for (int i = 0; i < elig.size(); i++) ids.append(i > 0 ? "," : "").append("\"").append(elig.get(i).getId()).append("\"");
                ids.append("]");
                String opts = "{\"attackers\":" + ids + ",\"defenders\":[\"" + esc(def.toString()) + "\"]}";
                String val = valuePart(decide("attackers", opts, "[]"));
                java.util.Set<Integer> chosen = new java.util.HashSet<>();
                java.util.regex.Matcher m = java.util.regex.Pattern.compile("\"(\\d+)\"").matcher(val);
                while (m.find()) chosen.add(Integer.parseInt(m.group(1)));   // quoted digits = attacker ids
                int n = 0;
                for (Card c : elig) if (chosen.contains(c.getId())) { combat.addAttacker(c, def); n++; }
                System.out.println("[bot] engine attacks with " + n + " of " + elig.size() + " eligible");
            } catch (Exception e) { /* no attack — legal, not Forge AI */ }
        }

        @Override
        public void declareBlockers(Player defender, Combat combat) {
            try {
                List<Card> attackers = new java.util.ArrayList<>(combat.getAttackers());
                if (attackers.isEmpty()) return;
                StringBuilder pairs = new StringBuilder("[");
                int np = 0;
                for (Card b : defender.getCreaturesInPlay()) {
                    if (b.isTapped()) continue;
                    for (Card a : attackers)
                        if (CombatUtil.canBlock(a, b, combat))
                            pairs.append(np++ > 0 ? "," : "").append("[\"").append(b.getId()).append("\",\"").append(a.getId()).append("\"]");
                }
                pairs.append("]");
                if (np == 0) return;
                String val = valuePart(decide("blockers", "{\"pairs\":" + pairs + "}", "[]"));
                java.util.regex.Matcher m = java.util.regex.Pattern.compile("\\[\"(\\d+)\",\"(\\d+)\"\\]").matcher(val);
                int n = 0;
                while (m.find()) {                              // [blockerId, attackerId]
                    Card b = bfCard(Integer.parseInt(m.group(1))), a = bfCard(Integer.parseInt(m.group(2)));
                    if (a != null && b != null) { combat.addBlocker(a, b); n++; }
                }
                System.out.println("[bot] engine declares " + n + " block(s)");
            } catch (Exception e) { /* no block — legal, not Forge AI */ }
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
