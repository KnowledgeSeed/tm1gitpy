## Változások összefoglaló (feature/gergo_changes)
### Lemaradt kódrészlet:
`tm1_git_py/changeset.py`: visszahoztam a rule és chore task-szintű változásrészletezést (C/D sorok `cubes/<cube>|area` és `chores/<chore>|process|index` formátumban), hogy a comparator által jelzett módosítások granuláltan kimutathatók és filterezhetők legyenek.

## Már a Main-ben lévő modosítások amik átjöttek:

### Filter
- `tm1_git_py/filter.py`: változatlan logika; a `normalize_for_path` továbbra is normalizálja a rule area-kat (kisbetű, speciális karakterek cseréje), és a szűrés `|`-del tagolt path minták alapján működik.
  - Példa szabályokra (`tm1_git_py/filter.txt`):  
    - `+/cubes/*` minden kockát enged, majd `-/chores/zSYS*` a zSYS-sel kezdődő chore-okat tiltja.  
    - Rule-szint: `-/cubes/Sales|[GP Subset]:[All Products]` letiltja a Sales kocka adott area-jához tartozó szabályt; a path normalizálva így néz ki: `cubes/sales|gpsubset_allproducts`.  
    - Task-szint: `-/chores/LoadChore|zLoadProcess|0` letiltja a `LoadChore` első (0. index) feladatát, amely a `zLoadProcess` folyamatot hívja. Index nélkül (`-/chores/LoadChore|zLoadProcess`) minden ilyen nevű taskot tilt a chore-ban.

### Rule kezelés
- `tm1_git_py/changeset.py`: a módosítások listájában újra megjelennek a rule-szintű sorok. Cube módosításnál, ha a `rules` set változik, a changeset `U /cubes/<cube>` mellett `C|D /cubes/<cube>|<normalize_for_path(area)>` sorokat generál.
- Comparator továbbra is a rule set-et hasonlítja (`Cube` equality override), így a rule-változás módosításként bekerül a changeset-be, majd a részletező sorokat a changeset állítja elő.
- Serializer/Deserializer: nincs változtatás, továbbra is be-/kiírja a `.rules` fájlt, ezért a rule-k filterezhetők és kimutathatók.

### Task kezelés
- `tm1_git_py/changeset.py`: chore módosításnál, ha a task lista változik, a changeset `U /chores/<chore>` mellett `C|D /chores/<chore>|<process_name>|<index>` sorokat generál.
- Comparator a `Chore.__eq__`-t használja, amely figyelembe veszi a `tasks` tartalmát, így a task-változás módosításként bekerül.
- `task.py` érintetlen; a chore-k feladatai deszerializálva/serializálva változatlan formátumban maradnak, ezért továbbra is filterezhetők és kimutathatók.
