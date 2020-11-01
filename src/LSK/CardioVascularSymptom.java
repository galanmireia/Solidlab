package LSK;

public class CardioVascularSymptom extends Symptom implements 	Curable {

	public CardioVascularSymptom(int covidImpact, int severityIndex, String name) {
		super(covidImpact, severityIndex, name);
		// TODO Auto-generated constructor stub
	}

	@Override
	public void cure() {
		// TODO Auto-generated method stub
		System.out.println("Sendatu egin da CardioVascularSymptom");
	}



}
