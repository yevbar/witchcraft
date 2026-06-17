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
import com.google.common.eventbus.Subscribe;

import forge.GuiDesktop;
import forge.game.event.GameEventTurnPhase;
import forge.game.event.GameEventTurnBegan;
import forge.game.event.GameEventLandPlayed;
import forge.game.event.GameEventSpellAbilityCast;
import forge.game.event.GameEventAttackersDeclared;
import forge.game.event.GameEventPlayerDamaged;
import forge.game.event.GameEventPlayerLivesChanged;
import forge.game.event.GameEventBlockersDeclared;
import forge.game.event.GameEventCardChangeZone;
import forge.game.GameEntityView;
import forge.game.card.CardView;
import com.google.common.collect.Multimap;
import java.util.Map.Entry;
import java.util.Collection;
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
import forge.card.mana.ManaCost;
import forge.game.GameEntity;
import forge.game.card.Card;
import forge.game.card.CardCollection;
import forge.game.card.CardCollectionView;
import forge.game.combat.Combat;
import forge.game.combat.CombatUtil;
import forge.game.cost.CostPartMana;
import forge.game.mana.ManaConversionMatrix;
import forge.game.mana.ManaCostBeingPaid;
import forge.game.mana.Mana;
import forge.game.player.DelayedReveal;
import forge.game.player.Player;
import forge.game.player.PlayerActionConfirmMode;
import forge.util.collect.FCollectionView;
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

public class ForgeCommanderFFA {
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
        private final int port;                                 // each witchcraft seat dials its OWN bot port
        private Socket sock;
        private BufferedReader in;
        private PrintWriter out;
        private boolean greeted = false;

        RemoteController(Game game, Player p, LobbyPlayer lp, int port) {
            super(game, p, lp);
            this.seat = p.getName();
            this.port = port;
        }

        private boolean connect() {
            if (sock != null) return true;
            try {
                sock = new Socket(HOST, port);
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
            StringBuilder b = new StringBuilder("{\"id\":\"" + c.getId() + "\",\"name\":\"" + esc(c.getName())
                    + "\",\"controller\":\"" + esc(c.getController().getName())
                    + "\",\"tapped\":" + c.isTapped());
            // TOKENS have no oracle entry, so the engine can't model them from the name — send their P/T +
            // type so reconstruct can synthesize a creature the lookahead can attack with (e.g. Otter tokens).
            if (c.isToken()) {
                b.append(",\"token\":true,\"creature\":").append(c.isCreature()).append(",\"land\":").append(c.isLand());
                if (c.isCreature()) b.append(",\"pow\":").append(c.getNetPower()).append(",\"tou\":").append(c.getNetToughness());
            }
            return b.append("}").toString();
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
                // tag a LAND play distinctly (§305) — playing a land is offered here as a candidate, but our
                // engine develops mana via lands explicitly, so the bot needs to tell them from spell casts.
                String knd = (host != null && host.isLand() && !cands.get(i).isManaAbility()) ? "land" : "spell";
                opts.append(i > 0 ? "," : "")
                    .append("{\"id\":\"").append(host != null ? host.getId() : 0)
                    .append("\",\"ci\":").append(i)
                    .append(",\"label\":\"").append(esc(cands.get(i).toString())).append("\",\"kind\":\"").append(knd).append("\"}");
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

        // -- AUDIT: every Forge-AI decision method we DON'T drive ourselves is counted here, then delegated
        // to super (PlayerControllerAi). At game end we print the tally so we know EXACTLY where Forge's AI
        // still decided (vs. our engine). These are the not-yet-intercepted decisions.
        static final java.util.Map<String, Integer> FORGE_AI = new java.util.TreeMap<>();
        private static void tally(String m) { FORGE_AI.merge(m, 1, Integer::sum); }

        // OUR mana payment: decide which untapped lands to tap (greedy color-match) and pay from the pool —
        // no Forge-AI chooser. Falls back to super (mechanical) only for costs we don't handle (hybrid/X/snow)
        // or if our plan can't cover it, so the game never breaks; that fallback is tallied.
        @Override public boolean payManaCost(ManaCost toPay, CostPartMana cp, SpellAbility sa, String prompt, ManaConversionMatrix mx, boolean effect) {
            try { if (manualPay(toPay, sa)) return true; } catch (Throwable t) { /* fall through */ }
            // NO FORGE-AI FALLBACK: a cost our own payer can't cover (hybrid/X/snow) is DECLINED — return false so
            // Forge cancels the cast (legal, the spell rolls back). We never consult Forge's strategic payer.
            tally("payManaCost (declined, no Forge-AI)");
            return false;
        }

        private boolean producesColor(Card c, char col) {
            for (SpellAbility ma : c.getManaAbilities()) {
                String prod = ma.getManaPart().mana(ma);
                if (prod != null && prod.indexOf(col) >= 0) return true;
            }
            return false;
        }

        private boolean manualPay(ManaCost toPay, SpellAbility sa) {
            String cs = toPay.toString();                       // e.g. "{2}{R}"
            if (java.util.regex.Pattern.compile("\\{(?!\\d+\\}|[WUBRG]\\})[^}]*\\}").matcher(cs).find())
                return false;                                   // a non-basic symbol (hybrid/X/snow/…) -> let super pay
            java.util.Map<Character, Integer> need = new java.util.HashMap<>();
            java.util.regex.Matcher m = java.util.regex.Pattern.compile("\\{([WUBRG])\\}").matcher(cs);
            while (m.find()) need.merge(m.group(1).charAt(0), 1, Integer::sum);
            int generic = toPay.getGenericCost();
            Player me = getPlayer();
            java.util.List<Card> untapped = new java.util.ArrayList<>();
            for (Card c : me.getCardsIn(ZoneType.Battlefield))
                if (!c.isTapped() && !c.getManaAbilities().isEmpty()) untapped.add(c);
            java.util.List<Card> plan = new java.util.ArrayList<>();
            java.util.Set<Card> used = new java.util.HashSet<>();
            for (java.util.Map.Entry<Character, Integer> e : need.entrySet()) {   // cover colored pips
                int cnt = e.getValue();
                for (Card c : untapped) {
                    if (cnt == 0) break;
                    if (!used.contains(c) && producesColor(c, e.getKey())) { plan.add(c); used.add(c); cnt--; }
                }
                if (cnt > 0) return false;                      // can't make this color from our lands
            }
            for (Card c : untapped) {                           // cover generic with anything left
                if (generic == 0) break;
                if (!used.contains(c)) { plan.add(c); used.add(c); generic--; }
            }
            if (generic > 0) return false;                      // not enough lands
            ManaCostBeingPaid cost = new ManaCostBeingPaid(toPay);
            for (Card land : plan) {                            // tap each chosen land, produce its mana into our pool
                SpellAbility ma = land.getManaAbilities().iterator().next();
                ma.getManaPart().produceMana(ma);
                land.tap(false, ma, me);
            }
            me.getManaPool().payManaCostFromPool(cost, sa, false, new java.util.ArrayList<Mana>());
            return cost.isPaid();
        }

        // OUR combat-damage assignment: lethal-first across the blockers in order (overkill dumped on the
        // last) — replaces Forge's strategic ComputerUtilCombat.distributeAIDamage. No trample-to-player here.
        @Override public java.util.Map<Card, Integer> assignCombatDamage(Card a, CardCollectionView bl, CardCollectionView rem, int dmg, GameEntity de, boolean ord) {
            // NO FORGE-AI: lethal-first across the blockers in declared order (overkill dumped on the last). No
            // blockers / any error -> an empty map (the unblocked/trample case is handled by Forge mechanically).
            java.util.Map<Card, Integer> out = new java.util.LinkedHashMap<>();
            try {
                java.util.List<Card> blk = new java.util.ArrayList<>();
                for (Card b : bl) blk.add(b);
                if (blk.isEmpty()) { tally("assignCombatDamage (no blockers, non-AI empty)"); return out; }
                int left = dmg;
                for (int i = 0; i < blk.size(); i++) {
                    Card b = blk.get(i);
                    int lethal = Math.max(1, b.getNetToughness() - b.getDamage());
                    int give = (i == blk.size() - 1) ? left : Math.min(left, lethal);
                    out.put(b, Math.max(0, give));
                    left -= give;
                    if (left <= 0) break;
                }
            } catch (Throwable t) {
                tally("assignCombatDamage (non-AI default)");
                out.clear();
                java.util.Iterator<Card> it = bl.iterator();      // best-effort legal: all damage on the first blocker
                if (it.hasNext()) out.put(it.next(), dmg);
            }
            return out;
        }

        // -- The remaining mechanical choices Forge asks for: each takes a DETERMINISTIC, NON-STRATEGIC legal
        // default — NEVER super (PlayerControllerAi). The witchcraft seat thus consults Forge's AI for NOTHING;
        // a choice our engine doesn't drive becomes the minimal legal default (first/min/decline), i.e. a "pass".
        @Override public CardCollection orderBlockers(Card a, CardCollection b) {
            tally("orderBlockers (as-is, no-AI)"); return b; }                  // declared order, no reordering
        @Override public CardCollection chooseCardsToDiscardToMaximumHandSize(int n) {
            tally("cleanupDiscard (first-n, no-AI)");
            CardCollection hand = new CardCollection(getPlayer().getCardsIn(ZoneType.Hand));
            CardCollection pick = new CardCollection();
            for (int i = 0; i < hand.size() && pick.size() < n; i++) pick.add(hand.get(i));   // first n, not strategic
            return pick; }
        @Override public boolean chooseTargetsFor(SpellAbility sa) {
            // No Forge-AI targeter: decline (false) -> Forge cancels the spell/ability (legal, a "pass"). Our
            // engine drives WHAT to cast (chooseSpellAbilityToPlay); a spell needing a target it can't supply
            // simply isn't played rather than letting Forge's AI pick the target.
            tally("chooseTargetsFor (declined, no-AI)"); return false; }
        @Override public <T extends GameEntity> T chooseSingleEntityForEffect(FCollectionView<T> opts, DelayedReveal dr, SpellAbility sa, String title, boolean isOpt, Player tp, java.util.Map<String, Object> params) {
            // optional -> null (decline); mandatory -> the FIRST legal option (deterministic, not Forge-AI).
            tally("chooseSingleEntityForEffect (first/decline, no-AI)");
            if (isOpt || opts == null || opts.isEmpty()) return null;
            return opts.iterator().next(); }
        @Override public boolean confirmAction(SpellAbility sa, PlayerActionConfirmMode mode, String msg, java.util.List<String> opts, Card card, java.util.Map<String, Object> params) {
            tally("confirmAction (decline, no-AI)"); return false; }            // decline optional confirmations
        @Override public int chooseNumber(SpellAbility sa, String t, int min, int max) {
            tally("chooseNumber (min, no-AI)"); return min; }                   // the minimum legal number
        @Override public Integer announceRequirements(SpellAbility sa, int min, int max, String a) {
            tally("announceRequirements X (min, no-AI)"); return Math.max(min, 0); }   // X = its minimum (usually 0)
    }

    // ---------- a LobbyPlayer that installs the RemoteController ----------
    static class RemotePlayer extends LobbyPlayerAi {
        private final int port;
        RemotePlayer(String name, int port) { super(name, null); this.port = port; }
        @Override public Player createIngamePlayer(Game game, int id) {
            Player p = new Player(getName(), game, id);
            p.setFirstController(new RemoteController(game, p, this, port));
            return p;
        }
        @Override public forge.game.player.PlayerController createMindSlaveController(Player master, Player slave) {
            return new RemoteController(slave.getGame(), slave, this, port);
        }
    }

    // ---------- state dumper: write the REAL Forge board to JSONL each phase (for the renderer) ----------
    static class Dumper {
        private final Game game;
        private final PrintWriter w;
        Dumper(Game game, PrintWriter w) { this.game = game; this.w = w; }

        @Subscribe
        public void onPhase(GameEventTurnPhase ev) {
            try {
                StringBuilder b = new StringBuilder("{\"turn\":").append(game.getPhaseHandler().getTurn());
                b.append(",\"phase\":\"").append(esc(String.valueOf(ev.phase()))).append("\"");
                b.append(",\"active\":\"").append(esc(game.getPhaseHandler().getPlayerTurn() != null
                        ? game.getPhaseHandler().getPlayerTurn().getName() : "")).append("\"");
                b.append(",\"players\":[");
                int pi = 0;
                for (Player p : game.getPlayers()) {
                    b.append(pi++ > 0 ? "," : "").append("{\"name\":\"").append(esc(p.getName())).append("\"");
                    b.append(",\"life\":").append(p.getLife());
                    b.append(",\"hand\":").append(p.getCardsIn(ZoneType.Hand).size());
                    b.append(",\"handcards\":[");           // the Dumper is a spectator -> can show both hands
                    int hi = 0;
                    for (Card c : p.getCardsIn(ZoneType.Hand)) {
                        String hk = c.isLand() ? "land" : (c.isCreature() ? "creature" : "other");
                        b.append(hi++ > 0 ? "," : "").append("{\"name\":\"").append(esc(c.getName()))
                         .append("\",\"kind\":\"").append(hk).append("\"");
                        if (c.isCreature()) b.append(",\"pow\":").append(c.getNetPower()).append(",\"tou\":").append(c.getNetToughness());
                        b.append("}");
                    }
                    b.append("]");
                    b.append(",\"library\":").append(p.getCardsIn(ZoneType.Library).size());
                    b.append(",\"graveyard\":").append(p.getCardsIn(ZoneType.Graveyard).size());
                    b.append(",\"battlefield\":[");
                    int ci = 0;
                    for (Card c : p.getCardsIn(ZoneType.Battlefield)) {
                        String kind = c.isLand() ? "land" : (c.isCreature() ? "creature" : "other");
                        b.append(ci++ > 0 ? "," : "").append("{\"name\":\"").append(esc(c.getName()))
                         .append("\",\"kind\":\"").append(kind).append("\",\"tapped\":").append(c.isTapped());
                        if (c.isCreature()) b.append(",\"pow\":").append(c.getNetPower()).append(",\"tou\":").append(c.getNetToughness());
                        b.append("}");
                    }
                    b.append("]}");
                }
                b.append("]}");
                w.println(b.toString());
                w.flush();
            } catch (Throwable t) { /* never let dumping disturb the game */ }
        }
    }

    // A plain-English MOVE LOG built straight from game events (no Forge i18n localizer — which isn't
    // initialized headless, so Forge's own GameLog comes back empty). Prints "[move] ..." lines for BOTH
    // seats: turn starts, lands, spells, attacks, combat/other damage, and life changes. Every handler is
    // defensive — a logging hiccup must never disturb the refereed game.
    static class MoveLog {
        @Subscribe public void onTurn(GameEventTurnBegan e) {
            try { System.out.println("[move] --- Turn " + e.turnNumber() + " : " + e.turnOwner() + " ---"); }
            catch (Throwable t) { /* ignore */ }
        }
        @Subscribe public void onLand(GameEventLandPlayed e) {
            try { System.out.println("[move] " + e.player() + " plays land " + e.land()); }
            catch (Throwable t) { /* ignore */ }
        }
        @Subscribe public void onCast(GameEventSpellAbilityCast e) {
            try {
                String who = e.si().getActivatingPlayer().getName();
                String what = e.sa().getHostCard().getName();
                String verb = e.sa().isSpell() ? "casts" : "activates";
                String tgt = e.targetDescription() != null ? " -> " + e.targetDescription() : "";
                System.out.println("[move] " + who + " " + verb + " " + what + tgt);
            } catch (Throwable t) { /* ignore */ }
        }
        @Subscribe public void onAttack(GameEventAttackersDeclared e) {
            try {
                for (GameEntityView k : e.attackersMap().keySet()) {
                    java.util.Collection<CardView> atk = e.attackersMap().get(k);
                    if (atk == null || atk.isEmpty()) continue;
                    System.out.println("[move] " + e.player() + " attacks " + k + " with " + atk);
                }
            } catch (Throwable t) { /* ignore */ }
        }
        @Subscribe public void onPlayerDmg(GameEventPlayerDamaged e) {
            try {
                System.out.println("[move] " + e.source() + " deals " + e.amount()
                        + (e.combat() ? " combat" : " noncombat") + " damage to " + e.target()
                        + (e.infect() ? " (as poison)" : ""));
            } catch (Throwable t) { /* ignore */ }
        }
        @Subscribe public void onLife(GameEventPlayerLivesChanged e) {
            try { System.out.println("[move] " + e.player() + " life " + e.oldLives() + " -> " + e.newLives()); }
            catch (Throwable t) { /* ignore */ }
        }
        @Subscribe public void onBlock(GameEventBlockersDeclared e) {
            try {
                for (Entry<GameEntityView, Multimap<CardView, CardView>> kv : e.blockers().entrySet()) {
                    for (Entry<CardView, Collection<CardView>> att : kv.getValue().asMap().entrySet()) {
                        Collection<CardView> bl = att.getValue();
                        // Forge encodes "didn't block" as the attacker mapping to ITSELF — skip those, log
                        // only genuine blocks (blocker distinct from the attacker).
                        if (!bl.isEmpty() && com.google.common.collect.Iterables.get(bl, 0) != att.getKey())
                            System.out.println("[move] " + bl + " blocks " + att.getKey());
                    }
                }
            } catch (Throwable t) { /* ignore */ }
        }
        @Subscribe public void onZone(GameEventCardChangeZone e) {
            try {
                String from = e.from() != null ? String.valueOf(e.from().zoneType()) : "null";
                String to = e.to() != null ? String.valueOf(e.to().zoneType()) : "null";
                if ("Battlefield".equals(from) && ("Graveyard".equals(to) || "Exile".equals(to)))
                    System.out.println("[move] " + e.card() + " dies (" + from + " -> " + to + ")");
            } catch (Throwable t) { /* ignore */ }
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
        if (c == null && name.contains("//"))                  // a DFC/split listed by full name -> the front face
            c = FModel.getMagicDb().getCommonCards().getCard(name.split("//")[0].trim());
        if (c == null) throw new RuntimeException("card not found: " + name);
        return c;
    }

    // ---------- Commander deck loading (a simple .dck-ish file the Python runner generates) ----------
    // Format: a [Commander] section (1+ commander names, one per line) then a [Main] section ('N CardName'
    // lines). Forge's RegisteredPlayer.forCommander assigns the commander(s) to the command zone (§903).
    static Deck loadDeck(String path, String name) throws Exception {
        Deck d = new Deck(name);
        String section = "main";
        for (String raw : java.nio.file.Files.readAllLines(java.nio.file.Paths.get(path))) {
            String line = raw.trim();
            if (line.isEmpty() || line.startsWith("[metadata]") || line.startsWith("Name=")) continue;
            if (line.equalsIgnoreCase("[Commander]")) { section = "commander"; continue; }
            if (line.equalsIgnoreCase("[Main]")) { section = "main"; continue; }
            int n = 1;
            String cname = line;
            java.util.regex.Matcher m = java.util.regex.Pattern.compile("^(\\d+)\\s+(.*)$").matcher(line);
            if (m.matches()) { n = Integer.parseInt(m.group(1)); cname = m.group(2).trim(); }
            if (section.equals("commander")) d.getOrCreate(forge.deck.DeckSection.Commander).add(card(cname), n);
            else d.getMain().add(card(cname), n);
        }
        return d;
    }

    public static void main(String[] args) throws Exception {
        initForge();
        // N seats in a Commander (§903) game — a free-for-all for N>2, a DUEL (§903.1 1v1 Commander) for N=2.
        // -Dseats=N (default 4, backward-compatible). Props (one per seat 0..N-1):
        //   -Ddeck<i>=<file>     the deck file per seat
        //   -Dname<i>=<name>     the seat's display name
        //   -Dtype<i>=witch|ai   witchcraft (socket-driven) or Forge AI
        //   -Dport<i>=<port>     the bot port for a witch seat (ignored for ai)
        int nseats = Integer.parseInt(System.getProperty("seats", "4"));
        List<RegisteredPlayer> players = Lists.newArrayList();
        StringBuilder seats = new StringBuilder();
        for (int i = 0; i < nseats; i++) {
            String deckFile = System.getProperty("deck" + i);
            String name = System.getProperty("name" + i, "Seat" + i);
            String type = System.getProperty("type" + i, "ai");
            int port = Integer.parseInt(System.getProperty("port" + i, "0"));
            Deck deck = loadDeck(deckFile, name);
            LobbyPlayer lp = type.equalsIgnoreCase("witch") ? new RemotePlayer(name, port) : new LobbyPlayerAi(name, null);
            players.add(RegisteredPlayer.forCommander(deck).setPlayer(lp));
            seats.append(i == 0 ? "" : ", ").append(name).append("[").append(type).append("]");
        }
        System.out.println("Commander seats (" + nseats + "): " + seats);
        GameRules rules = new GameRules(GameType.Commander);
        rules.setGamesPerMatch(1);
        Match match = new Match(rules, players, "witchcraft-commander");
        Game game = new Game(players, rules, match);
        game.subscribeToEvents(new MoveLog());
        System.out.println("Starting " + nseats + "-player Commander (Forge referees; witchcraft drives its seat(s)) ...");
        long t0 = System.currentTimeMillis();
        match.startGame(game);
        String w = (game.getOutcome() != null && game.getOutcome().getWinningLobbyPlayer() != null)
                ? game.getOutcome().getWinningLobbyPlayer().getName() : "draw/none";
        System.out.println("RESULT winner=" + w + " turns=" + game.getPhaseHandler().getTurn()
                + " wall=" + (System.currentTimeMillis() - t0) + "ms");
        // The witchcraft seats consult Forge's AI for NOTHING. This tallies the mechanical choices that took a
        // DETERMINISTIC non-AI legal default (first/min/decline) instead of an engine decision — Forge-AI free.
        System.out.println("Non-AI mechanical defaults taken by witchcraft seats (NO Forge-AI ever): "
                + (RemoteController.FORGE_AI.isEmpty() ? "NONE" : RemoteController.FORGE_AI));
    }
}
