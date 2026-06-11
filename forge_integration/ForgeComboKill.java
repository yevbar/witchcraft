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
import forge.game.cost.CostPayment;
import forge.ai.AiCostDecision;
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

public class ForgeComboKill {
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
            // §608/§702.40 spells cast THIS TURN (by anyone) — the storm count basis. The engine's lookahead
            // reads this back as _cast_count so its model's storm count stays in sync with Forge mid-turn.
            b.append("\"castThisTurn\":").append(g.getStack().getSpellsCastThisTurn().size()).append(",");
            // §103 each player's LIBRARY SIZE — the lookahead simulates from this state, so it needs the
            // library counts: our own for the §104 'win on empty library' check, the opponent's so it doesn't
            // fabricate a deck-out win. Just counts (contents stay hidden); the engine synthesizes placeholders.
            b.append("\"libCounts\":{");
            for (int i = 0; i < ps.size(); i++)
                b.append(i > 0 ? "," : "").append('"').append(esc(ps.get(i).getName())).append("\":")
                 .append(ps.get(i).getCardsIn(ZoneType.Library).size());
            b.append("},");
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
                        // Offer anything legal to play (timing/zone). We DON'T gate on Forge's
                        // ComputerUtilCost.canPayCost — that AI affordability check is conservative for
                        // any-color sacrifice sources (it won't see Black Lotus pay {U}{U}), and mana payment
                        // is now OUR engine's job (enginePay). Our lookahead only picks a spell it can afford
                        // in its own mana model, and enginePay then pays the exact way it intends.
                        if (sa.canPlay() && !sa.isManaAbility()) out.add(sa);
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
                SpellAbility sa = cands.get(ci);
                setComboTargets(sa);                            // the chosen SA is raw -> set its target before it hits the stack
                System.out.println("[bot] engine plays: " + sa + "  (of " + cands.size() + " options)");
                return Lists.newArrayList(sa);
            }
            return null;                                        // engine passed (or unreachable -> pass, NOT Forge AI)
        }

        // A raw candidate SA from getSpellAbilities() carries no targets; MagicStack.add rejects an
        // untargeted 'target player' spell ("failed to target"). Our engine drives targeting: aim a
        // player-targeting spell (Tendrils of Agony) at the opponent. Forge still enforces legality.
        private void setComboTargets(SpellAbility sa) {
            try {
                if (sa.usesTargeting() && sa.getTargetRestrictions() != null
                        && sa.getTargetRestrictions().canTgtPlayer()) {
                    for (Player opp : getGame().getPlayers()) {
                        if (opp != getPlayer() && sa.canTarget(opp)) {
                            sa.resetTargets();
                            sa.getTargets().add(opp);
                            return;
                        }
                    }
                }
            } catch (Throwable t) { /* leave untargeted -> Forge will reject, harmless */ }
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

        // OUR mana payment is DELEGATED to the witchcraft engine: ask it WHICH sources to tap/sacrifice and
        // what COLOR each should make (driver.mana_plan), then execute that exact plan in Forge — the precise
        // sources/colors a combo can hinge on (pay {B} from a Mox, not by sacrificing a Black Lotus needed
        // for a later {U}{U}). Falls back to super only for costs the engine can't cover or won't model
        // (hybrid/X/snow), so the game never breaks; that fallback is tallied.
        @Override public boolean payManaCost(ManaCost toPay, CostPartMana cp, SpellAbility sa, String prompt, ManaConversionMatrix mx, boolean effect) {
            try { if (enginePay(toPay, sa)) return true; } catch (Throwable t) { /* fall through */ }
            tally("payManaCost (fallback)");
            return super.payManaCost(toPay, cp, sa, prompt, mx, effect);
        }

        private static String colorName(char c) {
            switch (c) { case 'W': return "white"; case 'U': return "blue"; case 'B': return "black";
                         case 'R': return "red"; case 'G': return "green"; default: return ""; }
        }
        private static String colorLetter(String c) {
            switch (c) { case "white": return "W"; case "blue": return "U"; case "black": return "B";
                         case "red": return "R"; case "green": return "G"; default: return ""; }
        }

        // ask the engine for a payment plan and execute it. Each plan step = a source id + an optional color
        // to express (for any-color/bundle sources). We activate each chosen mana ability through Forge's own
        // cost-payment + resolution (so tap AND sacrifice costs are paid correctly — Lotus Petal, Black Lotus),
        // then pay the spell's cost from the pool that produced.
        private boolean enginePay(ManaCost toPay, SpellAbility sa) {
            String cs = toPay.toString();
            if (java.util.regex.Pattern.compile("\\{(?!\\d+\\}|[WUBRG]\\})[^}]*\\}").matcher(cs).find())
                return false;                                   // a non-basic symbol (hybrid/X/snow/…) -> let super pay
            java.util.Map<Character, Integer> pips = new java.util.HashMap<>();
            java.util.regex.Matcher m = java.util.regex.Pattern.compile("\\{([WUBRG])\\}").matcher(cs);
            while (m.find()) pips.merge(m.group(1).charAt(0), 1, Integer::sum);
            StringBuilder pj = new StringBuilder("{"); boolean first = true;
            for (java.util.Map.Entry<Character, Integer> e : pips.entrySet()) {
                pj.append(first ? "" : ",").append('"').append(colorName(e.getKey())).append("\":").append(e.getValue()); first = false;
            }
            pj.append("}");
            String costJson = "{\"pips\":" + pj + ",\"generic\":" + toPay.getGenericCost() + "}";
            String reply = decide("pay", costJson, "[]");
            if (reply == null) return false;

            // parse the plan: a sequence of (id, express) — keep order, Forge taps in that order.
            java.util.List<String[]> plan = new java.util.ArrayList<>();
            java.util.regex.Matcher pm = java.util.regex.Pattern.compile(
                    "\"id\"\\s*:\\s*\"([^\"]*)\"\\s*,\\s*\"express\"\\s*:\\s*\"([^\"]*)\"").matcher(reply);
            while (pm.find()) plan.add(new String[]{pm.group(1), pm.group(2)});
            if (plan.isEmpty()) return false;                   // engine declined -> let super pay

            Player me = getPlayer();
            ManaCostBeingPaid cost = new ManaCostBeingPaid(toPay);
            for (String[] step : plan) {
                Card src = null;
                for (Card c : me.getCardsIn(ZoneType.Battlefield))
                    if (String.valueOf(c.getId()).equals(step[0])) { src = c; break; }
                if (src == null || src.getManaAbilities().isEmpty()) return false;
                SpellAbility ma = src.getManaAbilities().iterator().next();
                String express = colorLetter(step[1]);
                if (!express.isEmpty()) ma.getManaPart().setExpressChoice(express);   // force the engine's color
                CostPayment pay = new CostPayment(ma.getPayCosts(), ma);              // pays {T} AND Sacrifice
                if (!pay.payComputerCosts(new AiCostDecision(me, ma, false, true))) return false;
                me.getGame().getStack().addAndUnfreeze(ma);                           // resolve -> mana into pool
            }
            me.getManaPool().payManaCostFromPool(cost, sa, false, new java.util.ArrayList<Mana>());
            System.out.println("[bot] engine paid " + cs + " via " + plan.size() + " source(s) of its choosing");
            return cost.isPaid();
        }

        // OUR combat-damage assignment: lethal-first across the blockers in order (overkill dumped on the
        // last) — replaces Forge's strategic ComputerUtilCombat.distributeAIDamage. No trample-to-player here.
        @Override public java.util.Map<Card, Integer> assignCombatDamage(Card a, CardCollectionView bl, CardCollectionView rem, int dmg, GameEntity de, boolean ord) {
            try {
                java.util.List<Card> blk = new java.util.ArrayList<>();
                for (Card b : bl) blk.add(b);
                if (blk.isEmpty()) { tally("assignCombatDamage (no blockers->super)"); return super.assignCombatDamage(a, bl, rem, dmg, de, ord); }
                java.util.Map<Card, Integer> out = new java.util.LinkedHashMap<>();
                int left = dmg;
                for (int i = 0; i < blk.size(); i++) {
                    Card b = blk.get(i);
                    int lethal = Math.max(1, b.getNetToughness() - b.getDamage());
                    int give = (i == blk.size() - 1) ? left : Math.min(left, lethal);
                    out.put(b, Math.max(0, give));
                    left -= give;
                    if (left <= 0) break;
                }
                return out;
            } catch (Throwable t) {
                tally("assignCombatDamage (fallback)");
                return super.assignCombatDamage(a, bl, rem, dmg, de, ord);
            }
        }

        // §701.18 'choose a card name' (Demonic Consultation) — forward to the engine, which names the card
        // its LOOKAHEAD planned at cast time (the search picked it; we only relay). Empty reply -> Forge AI.
        private String engineCardName() {
            java.util.regex.Matcher m = java.util.regex.Pattern.compile("\"value\"\\s*:\\s*\"([^\"]*)\"")
                    .matcher(valuePart(decide("name", "[]", "\"\"")));
            return m.find() ? m.group(1) : "";
        }
        @Override public String chooseCardName(SpellAbility sa, java.util.function.Predicate<forge.card.ICardFace> cpp, String valid, String message) {
            String n = engineCardName();
            if (n != null && !n.isEmpty()) { System.out.println("[bot] engine names: " + n); return n; }
            tally("chooseCardName"); return super.chooseCardName(sa, cpp, valid, message); }
        @Override public String chooseCardName(SpellAbility sa, java.util.List<forge.card.ICardFace> faces, String message) {
            String n = engineCardName();
            if (n != null && !n.isEmpty()) { System.out.println("[bot] engine names: " + n); return n; }
            tally("chooseCardName"); return super.chooseCardName(sa, faces, message); }

        @Override public CardCollection orderBlockers(Card a, CardCollection b) {
            tally("orderBlockers"); return super.orderBlockers(a, b); }
        @Override public CardCollection chooseCardsToDiscardToMaximumHandSize(int n) {
            tally("cleanupDiscard"); return super.chooseCardsToDiscardToMaximumHandSize(n); }
        @Override public boolean chooseTargetsFor(SpellAbility sa) {
            // OUR engine drives targeting for the combo: a 'target player' spell (Tendrils of Agony, and
            // each of its storm copies) hits the opponent. Forge still enforces target legality (canTarget).
            try {
                if (sa.usesTargeting() && sa.getTargetRestrictions() != null
                        && sa.getTargetRestrictions().canTgtPlayer()) {
                    for (Player opp : getGame().getPlayers()) {
                        if (opp != getPlayer() && sa.canTarget(opp)) {
                            sa.resetTargets();
                            sa.getTargets().add(opp);
                            return true;
                        }
                    }
                }
            } catch (Throwable t) { /* fall through to Forge's chooser */ }
            tally("chooseTargetsFor"); return super.chooseTargetsFor(sa); }
        @Override public <T extends GameEntity> T chooseSingleEntityForEffect(FCollectionView<T> opts, DelayedReveal dr, SpellAbility sa, String title, boolean isOpt, Player tp, java.util.Map<String, Object> params) {
            tally("chooseSingleEntityForEffect"); return super.chooseSingleEntityForEffect(opts, dr, sa, title, isOpt, tp, params); }
        @Override public boolean confirmAction(SpellAbility sa, PlayerActionConfirmMode mode, String msg, java.util.List<String> opts, Card card, java.util.Map<String, Object> params) {
            tally("confirmAction"); return super.confirmAction(sa, mode, msg, opts, card, params); }
        @Override public int chooseNumber(SpellAbility sa, String t, int min, int max) {
            tally("chooseNumber"); return super.chooseNumber(sa, t, min, max); }
        @Override public Integer announceRequirements(SpellAbility sa, int min, int max, String a) {
            tally("announceRequirements (X)"); return super.announceRequirements(sa, min, max, a); }
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

    // The witchcraft combo deck: the cEDH Thassa's-Oracle line — Lotus Petal x3 for mana, Demonic
    // Consultation (name a card NOT in the deck -> exile the whole library), Thassa's Oracle (empty library
    // -> win). 5 cards, fits a real opening hand; the rest is filler the combo exiles. The lookahead drives
    // the whole thing (sequence + the 'name a card' choice) — nothing combo-specific in the bot. (3 Petals,
    // not Black Lotus + Mox Jet: each Petal is independent any-color mana, so Forge can't mis-pay one color.)
    static final String[] COMBO = {
        "Black Lotus", "Mox Jet", "Demonic Consultation", "Thassa's Oracle",
    };

    static Deck comboDeck(String name) {
        Deck d = new Deck(name);
        for (String c : COMBO) d.getMain().add(card(c));
        d.getMain().add(card("Island"), 60 - COMBO.length);
        return d;
    }

    // A do-nothing opponent: a pile of basic lands. The Forge AI just plays lands and passes, so the only
    // thing that decides the game is whether OUR engine pilots the storm kill.
    static Deck landsDeck(String name) {
        Deck d = new Deck(name);
        d.getMain().add(card("Mountain"), 60);
        return d;
    }

    // §103.5 CHEAT THE OPENING HAND — move the combo cards into the witchcraft seat's hand and shove the
    // rest back to the library, run from the startGameHook (after the opening draw, at the start of turn 1,
    // BEFORE any player gets priority). Forge owns every rule from here; we only fixed which 7→N cards we
    // opened with, exactly as a stacked playtest hand would.
    static void stackHand(Game game, Player p, String[] want) {
        java.util.List<String> need = new ArrayList<>(java.util.Arrays.asList(want));
        // pull each wanted card from wherever it is (hand or library) into the hand.
        for (Card c : new CardCollection(p.getCardsIn(ZoneType.Library))) {
            if (need.remove(c.getName())) game.getAction().moveToHand(c, null);
        }
        // anything still in hand that we didn't want -> bottom of library (keep the hand exactly `want`).
        java.util.List<String> keep = new ArrayList<>(java.util.Arrays.asList(want));
        for (Card c : new CardCollection(p.getCardsIn(ZoneType.Hand))) {
            if (!keep.remove(c.getName())) game.getAction().moveToLibrary(c, null);
        }
        StringBuilder b = new StringBuilder();
        for (Card c : p.getCardsIn(ZoneType.Hand)) b.append(b.length() > 0 ? ", " : "").append(c.getName());
        System.out.println("[stack] " + p.getName() + " opening hand (" + p.getCardsIn(ZoneType.Hand).size()
                + " cards): " + b);
    }

    public static void main(String[] args) {
        initForge();
        RegisteredPlayer witch = new RegisteredPlayer(comboDeck("witch")).setPlayer(new RemotePlayer("Witchcraft-Engine"));
        witch.setStartingHand(COMBO.length);                   // §103.4 enlarge the opening hand to fit the combo
        List<RegisteredPlayer> players = Lists.newArrayList();
        players.add(witch);
        players.add(new RegisteredPlayer(landsDeck("forge")).setPlayer(new LobbyPlayerAi("Forge-AI", null)));
        GameRules rules = new GameRules(GameType.Constructed);
        rules.setGamesPerMatch(1);
        Match match = new Match(rules, players, "witchcraft-combo-kill");
        Game game = new Game(players, rules, match);

        String dumpPath = System.getProperty("dump");
        if (dumpPath != null) {
            try {
                PrintWriter dw = new PrintWriter(new java.io.FileWriter(dumpPath));
                game.subscribeToEvents(new Dumper(game, dw));
                System.out.println("[dump] writing board snapshots to " + dumpPath);
            } catch (Exception e) { System.out.println("[dump] failed: " + e); }
        }

        // the hand-stacker hook: at the start of turn 1, force the witchcraft seat's opening hand to the combo.
        Runnable stacker = () -> {
            for (Player p : game.getPlayers()) {
                if (p.getName().equals("Witchcraft-Engine")) stackHand(game, p, COMBO);
            }
        };

        System.out.println("Starting: Witchcraft-Engine pilots a stacked turn-1 combo (lookahead-driven); Forge is the referee ...");
        long t0 = System.currentTimeMillis();
        match.startGame(game, stacker);                        // <- pass the stacker as the startGameHook
        String w = (game.getOutcome() != null && game.getOutcome().getWinningLobbyPlayer() != null)
                ? game.getOutcome().getWinningLobbyPlayer().getName() : "draw/none";
        StringBuilder lifeb = new StringBuilder();
        for (Player p : game.getRegisteredPlayers())
            lifeb.append(lifeb.length() > 0 ? ", " : "").append(p.getName()).append("=").append(p.getLife());
        System.out.println("RESULT winner=" + w + " turns=" + game.getPhaseHandler().getTurn()
                + " finalLife[" + lifeb + "]"
                + " wall=" + (System.currentTimeMillis() - t0) + "ms");
        System.out.println("FORGE-AI decisions still made for OUR seat (not our engine's): "
                + (RemoteController.FORGE_AI.isEmpty() ? "NONE" : RemoteController.FORGE_AI));
    }
}
