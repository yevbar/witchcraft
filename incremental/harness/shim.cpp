// shim.cpp — a tiny C ABI over a live SouffleProgram for the incremental-update harness.
//
// Drives a program compiled by the fork's souffle with --incremental, in-process via ctypes. Beyond the
// engine_inproc shim it adds what Phase 3 testing needs: insert into a NAMED relation without purging
// (to stage diff_plus_* tuples), call an arbitrary subroutine (executeSubroutine, e.g. "update"), and dump
// EVERY relation (input + internal + output), so the harness can compare full resident state — not just the
// .output relations — between an incremental update and a fresh recompute.
#include "souffle/SouffleInterface.h"
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
using namespace souffle;

// Parse a TSV blob ("rel\tf1\tf2\n...") and insert each row into its relation. No purge.
static void insert_blob(SouffleProgram* p, const char* facts) {
    std::string blob(facts);
    size_t i = 0, n = blob.size();
    while (i < n) {
        size_t nl = blob.find('\n', i);
        if (nl == std::string::npos) nl = n;
        if (nl > i) {
            std::string line = blob.substr(i, nl - i);
            size_t t0 = line.find('\t');
            std::string rel = (t0 == std::string::npos) ? line : line.substr(0, t0);
            Relation* r = p->getRelation(rel);
            if (r != nullptr) {
                size_t arity = r->getArity();
                tuple tup(r);
                size_t pos = (t0 == std::string::npos) ? line.size() : t0 + 1;
                size_t fi = 0;
                bool ok = true;
                // Fill the columns the row provides; pad any remaining columns (e.g. the @count/@iteration
                // auxiliary columns under --incremental, which the row omits) with the type's default.
                for (; fi < arity; fi++) {
                    char ty = *r->getAttrType(fi);
                    bool have = (pos <= line.size());
                    std::string field;
                    if (have) {
                        size_t nt = line.find('\t', pos);
                        size_t end = (nt == std::string::npos) ? line.size() : nt;
                        field = line.substr(pos, end - pos);
                        pos = (nt == std::string::npos) ? line.size() + 1 : end + 1;
                    }
                    if (!have) {
                        if (ty == 's') tup << std::string("");
                        else tup << (RamSigned) 0;
                    } else if (ty == 's') {
                        tup << field;
                    } else {
                        try { tup << (RamSigned) std::stoll(field); }
                        catch (...) { ok = false; break; }
                    }
                }
                if (ok && fi == arity) r->insert(tup);
            }
        }
        i = nl + 1;
    }
}

// Serialize a set of relations as TSV, DATA columns only (getArity() includes the @count/@iteration
// auxiliary columns; getAuxiliaryArity() is how many trailing columns to drop). Comparing data columns is
// the oracle: incremental state and a fresh recompute must agree on the derived facts, not on internal aux.
static char* serialize(SouffleProgram* p, const std::vector<Relation*>& rels) {
    std::string out;
    for (Relation* r : rels) {
        if (r == nullptr) continue;
        const std::string name = r->getName();
        size_t arity = r->getArity() - r->getAuxiliaryArity();
        for (auto& tup : *r) {
            out += name;
            for (size_t k = 0; k < arity; k++) {
                out += '\t';
                char ty = *r->getAttrType(k);
                RamDomain v = tup[k];
                if (ty == 's') out += r->getSymbolTable().decode(v);
                else out += std::to_string(v);
            }
            out += '\n';
        }
    }
    char* res = (char*) malloc(out.size() + 1);
    memcpy(res, out.data(), out.size());
    res[out.size()] = '\0';
    return res;
}

extern "C" {

void* h_create(const char* name) { return (void*) ProgramFactory::newInstance(std::string(name)); }
void h_destroy(void* h) { delete (SouffleProgram*) h; }
void h_free(char* s) { free(s); }

// Full Bootstrap: purge everything, insert all input facts, run the from-scratch fixpoint.
void h_bootstrap(void* h, const char* facts) {
    SouffleProgram* p = (SouffleProgram*) h;
    p->purgeInputRelations();
    p->purgeInternalRelations();
    p->purgeOutputRelations();
    insert_blob(p, facts);
    p->run();
}

// Stage tuples into named relations without purging (e.g. diff_plus_* before calling update).
void h_insert(void* h, const char* facts) { insert_blob((SouffleProgram*) h, facts); }

// Purge named relations (newline-separated) — the driver owns the staging relations' lifecycle, since an
// in-subroutine ram::Clear of a non-temporary relation is gated on pruneImdtRels (unset under a subroutine).
void h_purge(void* h, const char* names) {
    SouffleProgram* p = (SouffleProgram*) h;
    std::string nb(names);
    size_t i = 0, n = nb.size();
    while (i < n) {
        size_t nl = nb.find('\n', i);
        if (nl == std::string::npos) nl = n;
        if (nl > i) {
            Relation* r = p->getRelation(nb.substr(i, nl - i));
            if (r != nullptr) r->purge();
        }
        i = nl + 1;
    }
}

// Invoke a subroutine (e.g. "update") with no args.
void h_subroutine(void* h, const char* name) {
    SouffleProgram* p = (SouffleProgram*) h;
    std::vector<RamDomain> args, ret;
    p->executeSubroutine(std::string(name), args, ret);
}

// Purge every staging relation (diff_plus_*, diff_minus_*, __dirty_*) in one pass — the per-update reset the
// driver owes (in-subroutine Clear of a non-temporary is unreliable). Far cheaper than h_purge over a
// newline-list of ~3xN names: no name marshaling and no per-name map lookup, just iterate + prefix-test.
void h_purge_staging(void* h) {
    SouffleProgram* p = (SouffleProgram*) h;
    for (Relation* r : p->getAllRelations()) {
        const std::string& nm = r->getName();
        if (nm.rfind("diff_plus_", 0) == 0 || nm.rfind("diff_minus_", 0) == 0 || nm.rfind("__dirty_", 0) == 0) {
            r->purge();
        }
    }
}

// Dump every relation the program exposes (input + internal + output), data columns only.
char* h_dump_all(void* h) {
    SouffleProgram* p = (SouffleProgram*) h;
    return serialize(p, p->getAllRelations());
}

// Dump a single named relation (newline-separated names allowed).
char* h_dump(void* h, const char* names) {
    SouffleProgram* p = (SouffleProgram*) h;
    std::vector<Relation*> rels;
    std::string nb(names);
    size_t i = 0, n = nb.size();
    while (i < n) {
        size_t nl = nb.find('\n', i);
        if (nl == std::string::npos) nl = n;
        if (nl > i) rels.push_back(p->getRelation(nb.substr(i, nl - i)));
        i = nl + 1;
    }
    return serialize(p, rels);
}

}  // extern "C"
