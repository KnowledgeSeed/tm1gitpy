from typing import Dict, Any


class TI:
    def __init__(self, prolog_procedure, metadata_procedure, data_procedure, epilog_procedure):
        self.prolog_procedure = prolog_procedure
        self.metadata_procedure = metadata_procedure
        self.data_procedure = data_procedure
        self.epilog_procedure = epilog_procedure

    @classmethod
    def get_string_between(cls, text: str, start_keyword, end_keyword):
        start_idx = text.find(start_keyword)
        end_idx = text.find(end_keyword, start_idx)

        if start_idx != -1 and end_idx != -1:
            start_idx += len(start_keyword)
            result = text[start_idx:end_idx]
            return result
        else:
            print("Keywords not found")

    def __eq__(self, other):
        if not isinstance(other, TI):
            return NotImplemented

        if TI.normalize_text_for_equality(self.prolog_procedure) != TI.normalize_text_for_equality(other.prolog_procedure):
            return False

        if TI.normalize_text_for_equality(self.metadata_procedure) != TI.normalize_text_for_equality(other.metadata_procedure):
            return False

        if TI.normalize_text_for_equality(self.data_procedure) != TI.normalize_text_for_equality(other.data_procedure):
            return False

        if TI.normalize_text_for_equality(self.epilog_procedure) != TI.normalize_text_for_equality(other.epilog_procedure):
            return False

        return True

    def __hash__(self):
        return hash(tuple(sorted(self.to_dict().items())))

    def to_dict(self):
        return {
            'prolog_procedure': self.prolog_procedure,
            'metadata_procedure': self.metadata_procedure,
            'data_procedure': self.data_procedure,
            'epilog_procedure': self.epilog_procedure,
        }

    @classmethod
    def normalize_text(cls, text: str) -> str:
        return text.replace('\r\n', '\n').replace('\r', '\n')

    @classmethod
    def normalize_text_for_equality(cls, text: str) -> str:
        return cls.normalize_text(text.strip()).strip()

    @classmethod
    def from_string(cls, ti):
        return TI(
            cls.get_string_between(ti, '#region Prolog', '#endregion').strip(),
            cls.get_string_between(
                ti, '#region Metadata', '#endregion').strip(),
            cls.get_string_between(ti, '#region Data', '#endregion').strip(),
            cls.get_string_between(ti, '#region Epilog', '#endregion').strip())

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TI":
        return cls(
            data.get("prolog_procedure", ""),
            data.get("metadata_procedure", ""),
            data.get("data_procedure", ""),
            data.get("epilog_procedure", "")
        )

    def ti_as_string(self):
        line_sep = "\n"
        sections = [
            "#region Prolog",
            TI.normalize_text(self.prolog_procedure),
            "#endregion",
            "#region Metadata",
            TI.normalize_text(self.metadata_procedure),
            "#endregion",
            "#region Data",
            TI.normalize_text(self.data_procedure),
            "#endregion",
            "#region Epilog",
            TI.normalize_text(self.epilog_procedure),
            "#endregion"
        ]
        return line_sep.join(sections)
